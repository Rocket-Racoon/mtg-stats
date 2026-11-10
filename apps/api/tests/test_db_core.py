"""Unit tests for ``app.core.db`` internals (the AC tests are written separately)."""

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.schema import CreateIndex, CreateTable

from app.core import db
from app.core.db import AppSession, after_commit
from tests.factories import create
from tests.sample_models import Part, SampleBase, Widget, WidgetFactory

API_ROOT = Path(__file__).resolve().parents[1]


def _constraint_and_index_names() -> set[str]:
    names: set[str] = set()
    for table in SampleBase.metadata.tables.values():
        names |= {str(c.name) for c in table.constraints}
        names |= {str(i.name) for i in table.indexes}
    return names


def _ddl() -> list[str]:
    out = [str(CreateTable(t)) for t in SampleBase.metadata.sorted_tables]
    out += [str(CreateIndex(i)) for t in SampleBase.metadata.sorted_tables for i in t.indexes]
    return out


def test_naming_convention_is_conventional_and_stable_across_renders() -> None:
    assert _ddl() == _ddl()
    assert _constraint_and_index_names() == _constraint_and_index_names()
    assert {
        "pk_sample_widgets",
        "uq_sample_widgets_name",
        "ck_sample_widgets_name_not_empty",
        "ix_sample_widgets_name_kind",
        "pk_sample_parts",
        "fk_sample_parts_widget_id_sample_widgets",
    } <= _constraint_and_index_names()
    assert Part.__table__.c.widget_id.foreign_keys  # type: ignore[attr-defined]
    assert Widget.__table__.c.id.type.__class__.__name__ == "UUID"  # type: ignore[attr-defined]


def test_importing_db_does_not_create_an_engine() -> None:
    code = (
        "import app.core.db as db; "
        "assert db.get_engine.cache_info().currsize == 0; "
        "assert db.get_sessionmaker.cache_info().currsize == 0"
    )
    env = {**os.environ, "APP_ENV": "test", "DATABASE_URL": os.environ["DATABASE_URL"]}
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        cwd=API_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


async def test_engine_is_lazy_uses_psycopg_and_dispose_resets_it() -> None:
    engine = db.get_engine()
    assert engine.dialect.driver == "psycopg"
    assert db.get_engine() is engine
    await db.dispose_engine()
    assert db.get_engine.cache_info().currsize == 0
    await db.dispose_engine()  # safe when nothing exists


async def test_ids_and_timestamps_are_filled_on_insert(sample_schema: AppSession) -> None:
    widget = await create(sample_schema, WidgetFactory)
    assert isinstance(widget, Widget)
    assert widget.id.version == 7
    assert widget.created_at.utcoffset() == timedelta(0)
    assert widget.updated_at.utcoffset() == timedelta(0)


async def test_updated_at_changes_on_update_and_created_at_does_not(
    sample_schema: AppSession,
) -> None:
    widget = await create(sample_schema, WidgetFactory, name="a")
    assert isinstance(widget, Widget)
    created, updated = widget.created_at, widget.updated_at
    widget.kind = "changed"
    await sample_schema.flush()
    assert widget.created_at == created
    assert widget.updated_at > updated


async def test_timestamps_round_trip_as_utc(sample_schema: AppSession) -> None:
    local = datetime(2026, 1, 1, 12, tzinfo=timezone(timedelta(hours=5)))
    widget = await create(sample_schema, WidgetFactory, created_at=local)
    sample_schema.expunge(widget)
    row = (await sample_schema.execute(select(Widget))).scalar_one()
    assert row.created_at == local
    assert row.created_at.tzinfo is UTC


def test_assigning_a_naive_datetime_raises() -> None:
    with pytest.raises(ValueError, match="naive datetime"):
        Widget(created_at=datetime(2026, 1, 1))  # noqa: DTZ001


def test_column_type_rejects_a_naive_datetime() -> None:
    with pytest.raises(ValueError, match="naive datetime"):
        db.UtcDateTime().process_bind_param(datetime(2026, 1, 1), None)  # type: ignore[arg-type]  # noqa: DTZ001


async def test_after_commit_runs_callbacks_in_order_after_commit(
    sample_schema: AppSession,
) -> None:
    calls: list[str] = []

    async def second() -> None:
        calls.append("second")

    after_commit(sample_schema, lambda: calls.append("first"))
    after_commit(sample_schema, second)
    assert calls == []
    await sample_schema.commit()
    assert calls == ["first", "second"]
    await sample_schema.commit()
    assert calls == ["first", "second"]


async def test_after_commit_callbacks_are_dropped_on_rollback(sample_schema: AppSession) -> None:
    calls: list[str] = []
    after_commit(sample_schema, lambda: calls.append("x"))
    await sample_schema.rollback()
    await sample_schema.commit()
    assert calls == []


async def test_after_commit_failure_does_not_stop_later_callbacks(
    sample_schema: AppSession,
) -> None:
    calls: list[str] = []

    def boom() -> None:
        raise RuntimeError("boom")

    after_commit(sample_schema, boom)
    after_commit(sample_schema, lambda: calls.append("later"))
    await sample_schema.commit()
    assert calls == ["later"]


async def test_after_commit_callbacks_are_dropped_when_commit_fails(
    sample_schema: AppSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    after_commit(sample_schema, lambda: calls.append("x"))

    async def failing_commit(self: AsyncSession) -> None:
        raise RuntimeError("commit failed")

    monkeypatch.setattr(AsyncSession, "commit", failing_commit)
    with pytest.raises(RuntimeError):
        await sample_schema.commit()
    assert calls == []
    assert db._AFTER_COMMIT_INFO_SLOT not in sample_schema.info
