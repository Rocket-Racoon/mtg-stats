"""Cancelling a request during ``commit`` must not lose the commit or its after-commit callbacks.

A real table with a deferred constraint trigger that sleeps makes the commit slow enough to be
cancelled mid-flight against Postgres. The table is committed for real and dropped afterwards.
"""

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator

import anyio
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.db import after_commit, dispose_engine, get_engine, get_session

SessionContext = contextlib.asynccontextmanager(get_session)


@pytest.fixture
async def slow_table(db_engine: AsyncEngine) -> AsyncIterator[str]:
    """A committed table whose deferred insert trigger sleeps 0.5 s at commit time."""
    async for name in _make_table(db_engine, fail=False):
        yield name


@pytest.fixture
async def failing_table(db_engine: AsyncEngine) -> AsyncIterator[str]:
    """Like ``slow_table`` but the trigger raises after sleeping, so the commit fails."""
    async for name in _make_table(db_engine, fail=True):
        yield name


async def _make_table(db_engine: AsyncEngine, *, fail: bool) -> AsyncIterator[str]:
    body = "PERFORM pg_sleep(0.5); " + ("RAISE EXCEPTION 'boom'; " if fail else "") + "RETURN NULL;"
    name = f"acc_cancel_{uuid.uuid4().hex[:12]}"
    function = f"{name}_fn"
    async with db_engine.begin() as conn:
        await conn.execute(text(f"CREATE TABLE {name} (id integer PRIMARY KEY)"))
        await conn.execute(
            text(
                f"CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS "
                f"$$ BEGIN {body} END $$"
            )
        )
        await conn.execute(
            text(
                f"CREATE CONSTRAINT TRIGGER {name}_trg AFTER INSERT ON {name} "
                f"DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {function}()"
            )
        )
    try:
        yield name
    finally:
        async with db_engine.begin() as conn:
            await conn.execute(text(f"DROP TABLE IF EXISTS {name}"))
            await conn.execute(text(f"DROP FUNCTION IF EXISTS {function}()"))
        await dispose_engine()


async def _handler(table: str, committing: asyncio.Event, calls: list[str]) -> None:
    async with SessionContext() as session:
        await session.execute(text(f"INSERT INTO {table} (id) VALUES (1)"))  # noqa: S608
        after_commit(session, lambda: calls.append("ran"))
        committing.set()  # leaving the block starts the (slow) commit


async def _assert_committed(db_engine: AsyncEngine, table: str, calls: list[str]) -> None:
    async with db_engine.connect() as conn:
        count = (await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()  # noqa: S608
    assert count == 1
    assert calls == ["ran"]
    assert get_engine().pool.checkedout() == 0  # type: ignore[attr-defined]


async def test_task_cancel_during_commit_completes_commit_and_callbacks(
    db_engine: AsyncEngine, slow_table: str
) -> None:
    committing, calls = asyncio.Event(), []
    task = asyncio.create_task(_handler(slow_table, committing, calls))
    await committing.wait()
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await _assert_committed(db_engine, slow_table, calls)


async def test_anyio_cancel_scope_during_commit_completes_commit_and_callbacks(
    db_engine: AsyncEngine, slow_table: str
) -> None:
    committing, calls = asyncio.Event(), []
    scopes: list[anyio.CancelScope] = []

    async def scoped() -> None:
        with anyio.CancelScope() as scope:
            scopes.append(scope)
            await _handler(slow_table, committing, calls)

    task = asyncio.create_task(scoped())
    await committing.wait()
    await asyncio.sleep(0.2)
    scopes[0].cancel()
    await task  # the scope absorbs the cancellation; no IllegalStateChangeError escapes
    assert scopes[0].cancelled_caught
    await _assert_committed(db_engine, slow_table, calls)


async def test_commit_failure_after_cancel_is_logged_and_nothing_runs(
    db_engine: AsyncEngine, failing_table: str, caplog: pytest.LogCaptureFixture
) -> None:
    committing, calls = asyncio.Event(), []
    with caplog.at_level(logging.ERROR, logger="app.core.db"):
        task = asyncio.create_task(_handler(failing_table, committing, calls))
        await committing.wait()
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    records = [r for r in caplog.records if "commit failed after its caller" in r.getMessage()]
    assert len(records) == 1
    assert calls == []
    async with db_engine.connect() as conn:
        count = (await conn.execute(text(f"SELECT count(*) FROM {failing_table}"))).scalar_one()  # noqa: S608
    assert count == 0
    assert get_engine().pool.checkedout() == 0  # type: ignore[attr-defined]
