"""Acceptance tests for S-1.4.2 (database session and base model), derived from the criteria.

Alembic autogenerate itself arrives with S-1.4.3; AC2 is covered here by rendering DDL and
constraint names from the metadata, which is what autogenerate consumes.
"""

import json
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import factory
import httpx
import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from sqlalchemy import MetaData, insert, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.schema import CreateIndex, CreateTable
from starlette.types import Receive, Scope, Send

from app.core import db
from app.core.db import (
    NAMING_CONVENTION,
    AppSession,
    Base,
    SessionDep,
    after_commit,
)
from app.core.ids import uuid7
from app.main import API_PREFIX, create_app
from tests.factories import create, create_batch
from tests.sample_models import Part, SampleBase, Widget, WidgetFactory

# ---------------------------------------------------------------- AC1: session dependency


class _Recording(AppSession):
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
        await conn.execute(
            text(
                f'create table "{table}" (id text primary key, v text, '
                f'constraint "{table}_v_uq" unique (v) deferrable initially deferred)'
            )
        )

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

    @router.post("/probe/deferred")
    async def deferred(session: SessionDep) -> dict[str, str]:
        # Both inserts succeed now; the deferred unique constraint fails at COMMIT.
        for key in ("d1", "d2"):
            await session.execute(
                text(f'insert into "{table}" values (:i, :v)'),  # noqa: S608
                {"i": key, "v": "same"},
            )
        return {"id": "deferred"}

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


async def test_commit_failure_at_commit_time_returns_error_envelope_and_leaves_no_row(
    probe: Probe,
) -> None:
    """A deferred constraint fails inside the dependency's commit, before the response is sent."""
    response = await _post(probe.app, f"{API_PREFIX}/probe/deferred")
    assert response.status_code == 500
    error = response.json()["error"]
    assert set(error) == {"code", "message", "details", "request_id"}
    assert error["code"] == "internal_error"
    assert await _ids_from_separate_connection(probe.engine, probe.table) == []
    (session,) = _Recording.registry
    assert session.close_calls >= 1


async def test_row_is_committed_before_the_response_starts(probe: Probe) -> None:
    seen_at_response_start: list[list[str]] = []

    async def spying_app(scope: Scope, receive: Receive, send: Send) -> None:
        async def spying_send(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                seen_at_response_start.append(
                    await _ids_from_separate_connection(probe.engine, probe.table)
                )
            await send(message)

        await probe.app(scope, receive, spying_send)

    transport = httpx.ASGITransport(app=spying_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        response = await client.post(f"{API_PREFIX}/probe/ok")
    assert response.status_code == 200
    assert seen_at_response_start == [["ok"]]


async def test_real_get_sessionmaker_builds_app_sessions_without_expire_on_commit() -> None:
    db.get_engine.cache_clear()
    db.get_sessionmaker.cache_clear()
    try:
        factory = db.get_sessionmaker()
        assert factory.class_ is AppSession
        assert factory.kw["expire_on_commit"] is False
        async with factory() as session:
            assert isinstance(session, AppSession)
            assert (await session.execute(text("select 1"))).scalar_one() == 1
    finally:
        await db.dispose_engine()
    assert db.get_engine.cache_info().currsize == 0
    assert db.get_sessionmaker.cache_info().currsize == 0


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
        pytest.skip("deferred to S-1.4.3 (#20) by product-owner decision")
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


def _per_table_names(metadata: MetaData) -> dict[str, list[str]]:
    return {
        table.name: sorted(
            [str(c.name) for c in table.constraints] + [str(i.name) for i in table.indexes]
        )
        for table in metadata.tables.values()
    }


def _per_table_ddl(metadata: MetaData) -> dict[str, list[str]]:
    """DDL lines per table, sorted: constraint order inside CREATE TABLE is not the contract."""
    dialect = postgresql.dialect()
    out: dict[str, list[str]] = {}
    for table in metadata.tables.values():
        lines = str(CreateTable(table).compile(dialect=dialect)).splitlines()
        for index in table.indexes:
            lines += str(CreateIndex(index).compile(dialect=dialect)).splitlines()
        out[table.name] = sorted(line.strip().rstrip(",") for line in lines if line.strip())
    return out


def test_names_and_ddl_are_identical_in_fresh_metadata_copies() -> None:
    def fresh() -> MetaData:
        copy = MetaData(naming_convention=NAMING_CONVENTION)
        for table in SampleBase.metadata.tables.values():
            table.to_metadata(copy)
        return copy

    a, b = fresh(), fresh()
    assert _per_table_names(a) == _per_table_names(b) == _per_table_names(SampleBase.metadata)
    assert _per_table_ddl(a) == _per_table_ddl(b) == _per_table_ddl(SampleBase.metadata)


_RENDER_SCRIPT = """
import json
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable
from tests.sample_models import SampleBase

dialect = postgresql.dialect()
out = {}
for name, table in sorted(SampleBase.metadata.tables.items()):
    lines = str(CreateTable(table).compile(dialect=dialect)).splitlines()
    for index in table.indexes:
        lines += str(CreateIndex(index).compile(dialect=dialect)).splitlines()
    out[name] = {
        "names": sorted(
            [str(c.name) for c in table.constraints] + [str(i.name) for i in table.indexes]
        ),
        "ddl": sorted(line.strip().rstrip(",") for line in lines if line.strip()),
    }
print(json.dumps(out, sort_keys=True))
"""


def test_names_and_ddl_are_identical_across_processes_with_different_hash_seeds() -> None:
    api_root = Path(__file__).resolve().parents[1]
    outputs = []
    for seed in ("0", "1", "12345"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "APP_ENV": "test"}
        result = subprocess.run(  # noqa: S603
            [sys.executable, "-c", _RENDER_SCRIPT],
            cwd=api_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        outputs.append(result.stdout)
    assert outputs[0] == outputs[1] == outputs[2]
    assert "pk_sample_widgets" in json.loads(outputs[0])["sample_widgets"]["names"]


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


async def _rolled_back_run(
    engine: AsyncEngine, work: Callable[[AppSession], Awaitable[Any]]
) -> Any:
    """Run ``work`` in a session inside a transaction that is rolled back, as the fixture does."""
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = async_sessionmaker(
            connection,
            class_=AppSession,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        )()
        try:
            await connection.run_sync(SampleBase.metadata.create_all)
            return await work(session)
        finally:
            await session.close()
            await transaction.rollback()


async def _row_count(session: AsyncSession) -> int:
    return (await session.execute(text("select count(*) from sample_widgets"))).scalar_one()


async def _table_exists_elsewhere(engine: AsyncEngine) -> bool:
    async with engine.connect() as conn:
        found = (await conn.execute(text("select to_regclass('public.sample_widgets')"))).scalar()
    return found is not None


async def _insert_shared_value_and_count(session: AppSession) -> tuple[int, int]:
    before = await _row_count(session)
    session.add(Widget(name="shared-unique-value"))
    await session.flush()
    return before, await _row_count(session)


async def test_isolation_second_run_does_not_see_the_first_runs_row(db_engine: AsyncEngine) -> None:
    """Same unique value, same table, two rolled-back runs back to back: each sees only its own."""
    first = await _rolled_back_run(db_engine, _insert_shared_value_and_count)
    second = await _rolled_back_run(db_engine, _insert_shared_value_and_count)
    assert first == (0, 1)
    assert second == (0, 1)


async def test_isolation_first_insert_of_shared_value(sample_schema: AppSession) -> None:
    assert await _insert_shared_value_and_count(sample_schema) == (0, 1)


async def test_isolation_second_insert_of_shared_value(sample_schema: AppSession) -> None:
    assert await _insert_shared_value_and_count(sample_schema) == (0, 1)


async def test_isolation_shared_value_really_collides_within_one_session(
    sample_schema: AppSession,
) -> None:
    sample_schema.add(Widget(name="shared-unique-value"))
    await sample_schema.flush()
    sample_schema.add(Widget(name="shared-unique-value"))
    with pytest.raises(IntegrityError):
        await sample_schema.flush()


async def test_rows_and_tables_vanish_after_the_transaction_is_rolled_back(
    db_engine: AsyncEngine,
) -> None:
    async def insert(session: AppSession) -> int:
        await create(session, WidgetFactory, name="vanishes")
        return await _row_count(session)

    assert await _rolled_back_run(db_engine, insert) == 1
    assert not await _table_exists_elsewhere(db_engine)


# ---------------------------------------------------------------- AC6: factories


async def test_create_returns_instance_in_the_session_with_visible_row(
    sample_schema: AppSession,
) -> None:
    widget = await create(sample_schema, WidgetFactory, name="from-factory")
    assert isinstance(widget, Widget)
    assert widget in sample_schema
    assert widget.id.version == 7
    found = (await sample_schema.execute(select(Widget).where(Widget.name == "from-factory"))).one()
    assert found[0] is widget


async def test_create_batch_adds_all_rows_to_the_session(sample_schema: AppSession) -> None:
    class SequencedWidgetFactory(WidgetFactory):
        name = factory.Sequence(lambda n: f"batch-{n}")

    widgets = await create_batch(sample_schema, SequencedWidgetFactory, 3)
    assert len(widgets) == 3
    assert all(w in sample_schema for w in widgets)
    assert await _row_count(sample_schema) == 3


async def test_factory_row_is_visible_in_the_session_then_gone_after_rollback(
    db_engine: AsyncEngine,
) -> None:
    async def create_and_look(session: AppSession) -> tuple[int, bool]:
        await create(session, WidgetFactory, name="rolled-back")
        return await _row_count(session), await _table_exists_elsewhere(db_engine)

    assert await _rolled_back_run(db_engine, create_and_look) == (1, False)
    assert not await _table_exists_elsewhere(db_engine)


async def test_factory_instance_can_be_referenced_by_a_foreign_key_in_the_same_session(
    sample_schema: AppSession,
) -> None:
    widget = await create(sample_schema, WidgetFactory, name="parent")
    part = Part(widget_id=widget.id)
    sample_schema.add(part)
    await sample_schema.flush()
    assert part.widget_id == widget.id


# ---------------------------------------------------------------- client fixture (rolled back)


async def test_route_write_through_the_client_fixture_is_rolled_back(
    api_app: FastAPI,
    client: httpx.AsyncClient,
    sample_schema: AppSession,
    db_engine: AsyncEngine,
) -> None:
    router = APIRouter(prefix=API_PREFIX)

    @router.post("/probe/widgets")
    async def add_widget(session: SessionDep) -> dict[str, str]:
        widget = Widget(name="via-route")
        session.add(widget)
        await session.flush()
        return {"id": str(widget.id)}

    api_app.include_router(router)
    response = await client.post(f"{API_PREFIX}/probe/widgets")
    assert response.status_code == 200
    # The route's write (committed as a savepoint release) is visible in the test's session...
    assert await _row_count(sample_schema) == 1
    # ...but nothing reached the database for other connections: even the table is uncommitted.
    assert not await _table_exists_elsewhere(db_engine)


# ---------------------------------------------------------------- AppSession / after_commit


async def test_bare_commit_runs_after_commit_callbacks_in_order(db_session: AppSession) -> None:
    events: list[str] = []

    async def second() -> None:
        events.append("two")

    after_commit(db_session, lambda: events.append("one"))
    after_commit(db_session, second)
    after_commit(db_session, lambda: events.append("three"))
    assert events == []  # nothing runs before the commit
    await db_session.commit()
    assert events == ["one", "two", "three"]


async def test_after_commit_callbacks_run_only_once(db_session: AppSession) -> None:
    events: list[str] = []
    after_commit(db_session, lambda: events.append("x"))
    await db_session.commit()
    await db_session.commit()
    assert events == ["x"]


async def test_rollback_drops_after_commit_callbacks(db_session: AppSession) -> None:
    events: list[str] = []
    after_commit(db_session, lambda: events.append("x"))
    await db_session.rollback()
    await db_session.commit()
    assert events == []


async def test_close_drops_after_commit_callbacks(db_session: AppSession) -> None:
    events: list[str] = []
    after_commit(db_session, lambda: events.append("x"))
    await db_session.close()
    await db_session.commit()
    assert events == []


async def test_callbacks_are_dropped_when_the_commit_fails(
    db_session: AppSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    after_commit(db_session, lambda: events.append("x"))

    async def boom(self: AsyncSession) -> None:
        raise RuntimeError("commit failed")

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "commit", boom)
        with pytest.raises(RuntimeError, match="commit failed"):
            await db_session.commit()
    await db_session.commit()
    assert events == []


async def test_failing_after_commit_callback_does_not_block_the_rest(
    db_session: AppSession,
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
    await db_session.commit()
    assert events == ["first", "last"]


async def test_after_commit_on_a_plain_async_session_raises_type_error(
    db_engine: AsyncEngine,
) -> None:
    async with AsyncSession(db_engine) as plain:
        with pytest.raises(TypeError, match="AppSession"):
            after_commit(plain, lambda: None)
