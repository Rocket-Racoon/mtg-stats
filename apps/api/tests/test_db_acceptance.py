"""Acceptance tests for S-1.4.2 (database session and base model), derived from the criteria.

Alembic autogenerate itself arrives with S-1.4.3; AC2 is covered here by rendering DDL and
constraint names from the metadata, which is what autogenerate consumes.
"""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

import factory
import httpx
import pytest
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from sqlalchemy import MetaData, insert, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.schema import CreateIndex, CreateTable

from app.core import db
from app.core.db import (
    NAMING_CONVENTION,
    Base,
    after_commit,
    commit_session,
    get_session,
    rollback_session,
)
from app.core.ids import uuid7
from app.main import API_PREFIX, create_app
from tests.factories import create, create_batch
from tests.sample_models import Part, SampleBase, Widget, WidgetFactory

# ---------------------------------------------------------------- AC1: session dependency

SessionDep = Annotated[AsyncSession, Depends(get_session)]


class _Recording(AsyncSession):
    """Session that records itself and how often ``close`` was awaited."""

    registry: list["_Recording"] = []  # noqa: RUF012

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.close_calls = 0
        _Recording.registry.append(self)

    async def close(self) -> None:
        self.close_calls += 1
        await super().close()


@dataclass
class Probe:
    app: FastAPI
    table: str
    engine: AsyncEngine


@pytest.fixture
async def probe(db_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Probe]:
    """A probe app plus a unique real table (it escapes the rollback fixture, so it is dropped)."""
    table = f"acc_probe_{uuid.uuid4().hex[:12]}"
    async with db_engine.begin() as conn:
        await conn.execute(text(f'create table "{table}" (id text primary key)'))

    _Recording.registry = []
    factory = async_sessionmaker(db_engine, class_=_Recording, expire_on_commit=False)
    monkeypatch.setattr(db, "get_sessionmaker", lambda: factory)

    router = APIRouter(prefix=API_PREFIX)

    async def _insert(session: AsyncSession, value: str) -> None:
        await session.execute(text(f'insert into "{table}" values (:i)'), {"i": value})  # noqa: S608

    @router.post("/probe/ok")
    async def ok(session: SessionDep) -> dict[str, str]:
        await _insert(session, "ok")
        return {"id": "ok"}

    @router.post("/probe/http-error")
    async def http_error(session: SessionDep) -> None:
        await _insert(session, "http")
        raise HTTPException(status_code=409, detail="nope")

    @router.post("/probe/crash")
    async def crash(session: SessionDep) -> None:
        await _insert(session, "crash")
        raise RuntimeError("boom")

    app = create_app()
    app.include_router(router)
    try:
        yield Probe(app=app, table=table, engine=db_engine)
    finally:
        async with db_engine.begin() as conn:
            await conn.execute(text(f'drop table if exists "{table}"'))


async def _post(app: FastAPI, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.post(path)


async def _ids_from_separate_connection(engine: AsyncEngine, table: str) -> list[str]:
    async with engine.connect() as conn:
        return sorted((await conn.execute(text(f'select id from "{table}"'))).scalars())  # noqa: S608


async def test_successful_handler_commits_row_visible_from_another_connection(
    probe: Probe,
) -> None:
    response = await _post(probe.app, f"{API_PREFIX}/probe/ok")
    assert response.status_code == 200
    assert response.json() == {"id": "ok"}
    assert await _ids_from_separate_connection(probe.engine, probe.table) == ["ok"]


async def test_successful_handler_closes_the_session(probe: Probe) -> None:
    await _post(probe.app, f"{API_PREFIX}/probe/ok")
    (session,) = _Recording.registry
    assert session.close_calls >= 1
    assert not session.in_transaction()


async def test_http_error_in_handler_rolls_back_and_returns_the_error(probe: Probe) -> None:
    response = await _post(probe.app, f"{API_PREFIX}/probe/http-error")
    assert response.status_code == 409
    assert await _ids_from_separate_connection(probe.engine, probe.table) == []


async def test_unhandled_exception_rolls_back_and_returns_500(probe: Probe) -> None:
    response = await _post(probe.app, f"{API_PREFIX}/probe/crash")
    assert response.status_code == 500
    assert await _ids_from_separate_connection(probe.engine, probe.table) == []


@pytest.mark.parametrize("path", ["http-error", "crash"])
async def test_failed_handler_closes_the_session(probe: Probe, path: str) -> None:
    await _post(probe.app, f"{API_PREFIX}/probe/{path}")
    (session,) = _Recording.registry
    assert session.close_calls >= 1
    assert not session.in_transaction()


async def test_each_request_gets_its_own_session(probe: Probe) -> None:
    await _post(probe.app, f"{API_PREFIX}/probe/ok")
    await _post(probe.app, f"{API_PREFIX}/probe/http-error")
    assert len(_Recording.registry) == 2
    assert _Recording.registry[0] is not _Recording.registry[1]


def test_engine_is_async_psycopg3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "get_engine", db.get_engine.__wrapped__)  # uncached: no global state
    engine = db.get_engine()
    assert engine.dialect.name == "postgresql"
    assert engine.dialect.driver == "psycopg"
    assert engine.dialect.is_async
    assert engine.url.drivername == "postgresql+psycopg"


def test_alembic_files_if_present_use_the_psycopg_driver() -> None:
    root = Path(__file__).resolve().parents[1]
    files = [p for p in root.glob("alembic*") if p.is_file()]
    files += list((root / "alembic").rglob("*.py"))
    if not files:
        pytest.skip("Alembic is configured in S-1.4.3")
    joined = "\n".join(p.read_text(encoding="utf-8") for p in files)
    assert "psycopg2" not in joined
    assert "asyncpg" not in joined


# ---------------------------------------------------------------- AC2: naming convention


def _constraint_names(metadata: MetaData) -> list[str]:
    names: list[str] = []
    for table in metadata.sorted_tables:
        names += sorted(str(c.name) for c in table.constraints)
        names += sorted(str(i.name) for i in table.indexes)
    return names


def _ddl(metadata: MetaData) -> str:
    dialect = postgresql.dialect()
    out: list[str] = []
    for table in metadata.sorted_tables:
        out.append(str(CreateTable(table).compile(dialect=dialect)))
        out += [str(CreateIndex(i).compile(dialect=dialect)) for i in table.indexes]
    return "\n".join(out)


def test_base_metadata_carries_the_naming_convention() -> None:
    assert Base.metadata.naming_convention == NAMING_CONVENTION
    assert set(NAMING_CONVENTION) == {"pk", "fk", "uq", "ck", "ix"}


def test_constraint_and_index_names_follow_the_convention() -> None:
    names = _constraint_names(SampleBase.metadata)
    assert sorted(names) == sorted(
        [
            "pk_sample_widgets",
            "uq_sample_widgets_name",
            "ck_sample_widgets_name_not_empty",
            "ix_sample_widgets_name_kind",
            "pk_sample_parts",
            "fk_sample_parts_widget_id_sample_widgets",
        ]
    )
    assert {n.split("_", 1)[0] for n in names} == {"pk", "fk", "uq", "ck", "ix"}


def test_names_and_ddl_are_identical_on_every_run() -> None:
    first = (_constraint_names(SampleBase.metadata), _ddl(SampleBase.metadata))
    second = (_constraint_names(SampleBase.metadata), _ddl(SampleBase.metadata))
    assert first == second


def test_names_and_ddl_are_identical_in_fresh_metadata_copies() -> None:
    def fresh() -> MetaData:
        copy = MetaData(naming_convention=NAMING_CONVENTION)
        for table in SampleBase.metadata.sorted_tables:
            table.to_metadata(copy)
        return copy

    a, b = fresh(), fresh()
    assert _constraint_names(a) == _constraint_names(b) == _constraint_names(SampleBase.metadata)
    assert _ddl(a) == _ddl(b) == _ddl(SampleBase.metadata)


# ---------------------------------------------------------------- AC3: UUID v7 ids


async def test_row_inserted_without_id_gets_application_generated_uuid7(
    sample_schema: AsyncSession,
) -> None:
    widget = Widget(name="a")
    assert widget.id is None  # neither database-generated nor assigned before insert
    sample_schema.add(widget)
    await sample_schema.flush()
    assert isinstance(widget.id, uuid.UUID)
    assert widget.id.version == 7
    assert widget.id.variant == uuid.RFC_4122


async def test_id_column_is_a_postgres_uuid(sample_schema: AsyncSession) -> None:
    data_type = (
        await sample_schema.execute(
            text(
                "select data_type from information_schema.columns "
                "where table_name = 'sample_widgets' and column_name = 'id'"
            )
        )
    ).scalar_one()
    assert data_type == "uuid"


async def test_ids_created_in_sequence_sort_in_creation_order(
    sample_schema: AsyncSession,
) -> None:
    created: list[uuid.UUID] = []
    for i in range(200):
        widget = Widget(name=f"w{i}")
        sample_schema.add(widget)
        await sample_schema.flush()
        created.append(widget.id)
    assert created == sorted(created)
    assert len(set(created)) == 200
    ordered = (await sample_schema.execute(select(Widget.id).order_by(Widget.id))).scalars().all()
    assert list(ordered) == created


def test_uuid7_generated_back_to_back_is_strictly_increasing() -> None:
    ids = [uuid7() for _ in range(5000)]
    assert all(i.version == 7 for i in ids)
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)


# ---------------------------------------------------------------- AC4: timestamps


async def test_timestamps_after_insert_are_timezone_aware_utc(
    sample_schema: AsyncSession,
) -> None:
    before = datetime.now(UTC)
    widget = Widget(name="t")
    sample_schema.add(widget)
    await sample_schema.flush()
    await sample_schema.refresh(widget)
    after = datetime.now(UTC)
    for value in (widget.created_at, widget.updated_at):
        assert value.tzinfo is not None
        assert value.utcoffset() == timedelta(0)
        assert before <= value <= after


async def test_timestamp_columns_are_timestamptz(sample_schema: AsyncSession) -> None:
    rows = (
        await sample_schema.execute(
            text(
                "select column_name, data_type from information_schema.columns "
                "where table_name = 'sample_widgets' "
                "and column_name in ('created_at', 'updated_at')"
            )
        )
    ).all()
    assert {name: kind for name, kind in rows} == {  # noqa: C416
        "created_at": "timestamp with time zone",
        "updated_at": "timestamp with time zone",
    }


async def test_update_advances_updated_at_but_not_created_at(
    sample_schema: AsyncSession,
) -> None:
    widget = Widget(name="u")
    sample_schema.add(widget)
    await sample_schema.flush()
    await sample_schema.refresh(widget)
    created, updated = widget.created_at, widget.updated_at
    widget.kind = "changed"
    await sample_schema.flush()
    await sample_schema.refresh(widget)
    assert widget.created_at == created
    assert widget.updated_at > updated
    assert widget.updated_at.utcoffset() == timedelta(0)


def test_assigning_naive_datetime_in_constructor_raises() -> None:
    with pytest.raises(ValueError, match="naive"):
        Widget(name="n", created_at=datetime(2026, 1, 1))  # noqa: DTZ001


@pytest.mark.parametrize("attribute", ["created_at", "updated_at"])
def test_assigning_naive_datetime_to_attribute_raises_immediately(attribute: str) -> None:
    widget = Widget(name="n")
    with pytest.raises(ValueError, match="naive"):
        setattr(widget, attribute, datetime(2026, 1, 1, 12))  # noqa: DTZ001


async def test_naive_datetime_in_core_insert_is_rejected(sample_schema: AsyncSession) -> None:
    statement = insert(Widget).values(
        id=uuid7(),
        name="core",
        kind="",
        created_at=datetime(2026, 1, 1),  # noqa: DTZ001
        updated_at=datetime.now(UTC),
    )
    with pytest.raises(StatementError, match="naive"):
        await sample_schema.execute(statement)


async def test_aware_non_utc_datetime_is_stored_and_read_back_as_utc(
    sample_schema: AsyncSession,
) -> None:
    moment = datetime(2026, 3, 4, 17, 30, tzinfo=timezone(timedelta(hours=5)))
    widget = Widget(name="tz", created_at=moment)
    sample_schema.add(widget)
    await sample_schema.flush()
    await sample_schema.refresh(widget)
    assert widget.created_at == moment
    assert widget.created_at.utcoffset() == timedelta(0)
    assert widget.created_at.hour == 12


# ---------------------------------------------------------------- AC5: test isolation
# Two tests insert the same unique value into the same table. If either saw the other's row it
# would fail on the pre-insert count. The module-level list records execution order so a run can
# prove both orders were exercised (see the run commands in the story report).

_ORDER: list[str] = []


async def _insert_shared_value_and_count(session: AsyncSession, label: str) -> None:
    _ORDER.append(label)
    before = (await session.execute(text("select count(*) from sample_widgets"))).scalar_one()
    assert before == 0
    session.add(Widget(name="shared-unique-value"))
    await session.flush()
    after = (await session.execute(text("select count(*) from sample_widgets"))).scalar_one()
    assert after == 1


async def test_isolation_first_insert_of_shared_value(sample_schema: AsyncSession) -> None:
    await _insert_shared_value_and_count(sample_schema, "A")


async def test_isolation_second_insert_of_shared_value(sample_schema: AsyncSession) -> None:
    await _insert_shared_value_and_count(sample_schema, "B")


async def test_isolation_shared_value_really_collides_within_one_session(
    sample_schema: AsyncSession,
) -> None:
    sample_schema.add(Widget(name="shared-unique-value"))
    await sample_schema.flush()
    sample_schema.add(Widget(name="shared-unique-value"))
    with pytest.raises(IntegrityError):
        await sample_schema.flush()


async def test_isolation_sample_tables_do_not_persist_after_a_test(db_engine: AsyncEngine) -> None:
    async with db_engine.connect() as conn:
        exists = (await conn.execute(text("select to_regclass('public.sample_widgets')"))).scalar()
    assert exists is None


# ---------------------------------------------------------------- AC6: factories


async def test_create_returns_instance_in_the_session_with_visible_row(
    sample_schema: AsyncSession,
) -> None:
    widget = await create(sample_schema, WidgetFactory, name="from-factory")
    assert isinstance(widget, Widget)
    assert widget in sample_schema
    assert widget.id.version == 7
    found = (await sample_schema.execute(select(Widget).where(Widget.name == "from-factory"))).one()
    assert found[0] is widget


async def test_create_batch_adds_all_rows_to_the_session(sample_schema: AsyncSession) -> None:
    class SequencedWidgetFactory(WidgetFactory):
        name = factory.Sequence(lambda n: f"batch-{n}")

    widgets = await create_batch(sample_schema, SequencedWidgetFactory, 3)
    assert len(widgets) == 3
    assert all(w in sample_schema for w in widgets)


async def test_factory_row_is_visible_then_rolled_back_with_the_session(
    db_engine: AsyncEngine, db_session: AsyncSession
) -> None:
    connection = await db_session.connection()
    await connection.run_sync(SampleBase.metadata.create_all)
    await create(db_session, WidgetFactory, name="rolled-back")
    count = (await db_session.execute(text("select count(*) from sample_widgets"))).scalar_one()
    assert count == 1
    # Nothing is visible from another connection while the test transaction is open.
    async with db_engine.connect() as other:
        exists = (await other.execute(text("select to_regclass('public.sample_widgets')"))).scalar()
    assert exists is None


async def test_factory_rows_are_gone_in_a_later_test(db_engine: AsyncEngine) -> None:
    async with db_engine.connect() as conn:
        exists = (await conn.execute(text("select to_regclass('public.sample_widgets')"))).scalar()
    assert exists is None


async def test_factory_instance_can_be_referenced_by_a_foreign_key_in_the_same_session(
    sample_schema: AsyncSession,
) -> None:
    widget = await create(sample_schema, WidgetFactory, name="parent")
    part = Part(widget_id=widget.id)
    sample_schema.add(part)
    await sample_schema.flush()
    assert part.widget_id == widget.id


# ---------------------------------------------------------------- after_commit


async def test_after_commit_callbacks_run_after_successful_commit_in_order(
    db_session: AsyncSession,
) -> None:
    events: list[str] = []

    async def second() -> None:
        events.append("two")

    after_commit(db_session, lambda: events.append("one"))
    after_commit(db_session, second)
    after_commit(db_session, lambda: events.append("three"))
    assert events == []  # nothing runs before the commit
    await commit_session(db_session)
    assert events == ["one", "two", "three"]


async def test_after_commit_callbacks_run_only_once(db_session: AsyncSession) -> None:
    events: list[str] = []
    after_commit(db_session, lambda: events.append("x"))
    await commit_session(db_session)
    await commit_session(db_session)
    assert events == ["x"]


async def test_after_commit_callbacks_are_dropped_on_rollback(db_session: AsyncSession) -> None:
    events: list[str] = []
    after_commit(db_session, lambda: events.append("x"))
    await rollback_session(db_session)
    await commit_session(db_session)
    assert events == []


async def test_after_commit_callbacks_are_dropped_when_the_commit_fails(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    after_commit(db_session, lambda: events.append("x"))

    async def boom() -> None:
        raise RuntimeError("commit failed")

    monkeypatch.setattr(db_session, "commit", boom)
    with pytest.raises(RuntimeError, match="commit failed"):
        await commit_session(db_session)
    monkeypatch.undo()
    await commit_session(db_session)
    assert events == []


async def test_failing_after_commit_callback_does_not_block_the_rest(
    db_session: AsyncSession,
) -> None:
    events: list[str] = []

    def bad() -> None:
        raise RuntimeError("callback failed")

    async def bad_async() -> None:
        raise RuntimeError("async callback failed")

    after_commit(db_session, lambda: events.append("first"))
    after_commit(db_session, bad)
    after_commit(db_session, bad_async)
    after_commit(db_session, lambda: events.append("last"))
    await commit_session(db_session)
    assert events == ["first", "last"]
