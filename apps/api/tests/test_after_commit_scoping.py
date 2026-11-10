"""``after_commit`` callbacks are scoped to the savepoint they were registered in (T-1.9.1.2)."""

import logging

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.db import AppSession, after_commit


async def test_callback_in_a_rolled_back_savepoint_is_dropped(db_session: AppSession) -> None:
    calls: list[str] = []
    after_commit(db_session, lambda: calls.append("outer"))
    nested = await db_session.begin_nested()
    after_commit(db_session, lambda: calls.append("inner"))
    await nested.rollback()
    await db_session.commit()
    assert calls == ["outer"]


async def test_callback_in_a_rolled_back_context_manager_savepoint_is_dropped(
    db_session: AppSession,
) -> None:
    calls: list[str] = []
    try:
        async with db_session.begin_nested():
            after_commit(db_session, lambda: calls.append("inner"))
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    await db_session.commit()
    assert calls == []


async def test_callback_in_a_released_savepoint_runs_after_the_outer_commit(
    db_session: AppSession,
) -> None:
    calls: list[str] = []
    async with db_session.begin_nested():
        after_commit(db_session, lambda: calls.append("inner"))
    assert calls == []  # a savepoint release is not a commit
    await db_session.commit()
    assert calls == ["inner"]


async def test_callback_in_an_explicitly_released_savepoint_runs_after_the_outer_commit(
    db_session: AppSession,
) -> None:
    calls: list[str] = []
    nested = await db_session.begin_nested()
    after_commit(db_session, lambda: calls.append("inner"))
    await nested.commit()
    assert calls == []
    await db_session.commit()
    assert calls == ["inner"]


async def test_inner_rolled_back_and_outer_released_runs_only_the_outer_callbacks(
    db_session: AppSession,
) -> None:
    calls: list[str] = []
    async with db_session.begin_nested():
        after_commit(db_session, lambda: calls.append("outer-savepoint"))
        inner = await db_session.begin_nested()
        after_commit(db_session, lambda: calls.append("inner-savepoint"))
        await inner.rollback()
        after_commit(db_session, lambda: calls.append("outer-savepoint-2"))
    await db_session.commit()
    assert calls == ["outer-savepoint", "outer-savepoint-2"]


async def test_rolling_back_the_outer_savepoint_drops_a_released_inner_savepoints_callbacks(
    db_session: AppSession,
) -> None:
    calls: list[str] = []
    outer = await db_session.begin_nested()
    async with db_session.begin_nested():
        after_commit(db_session, lambda: calls.append("inner"))
    await outer.rollback()
    await db_session.commit()
    assert calls == []


async def test_callbacks_run_in_registration_order_across_savepoints(
    db_session: AppSession,
) -> None:
    calls: list[str] = []
    after_commit(db_session, lambda: calls.append("1"))
    async with db_session.begin_nested():
        after_commit(db_session, lambda: calls.append("2"))
        async with db_session.begin_nested():
            after_commit(db_session, lambda: calls.append("3"))
        after_commit(db_session, lambda: calls.append("4"))
    after_commit(db_session, lambda: calls.append("5"))
    await db_session.commit()
    assert calls == ["1", "2", "3", "4", "5"]


async def test_full_rollback_drops_every_callback(db_session: AppSession) -> None:
    calls: list[str] = []
    await db_session.execute(text("select 1"))
    after_commit(db_session, lambda: calls.append("outer"))
    async with db_session.begin_nested():
        after_commit(db_session, lambda: calls.append("released"))
    await db_session.rollback()
    await db_session.commit()
    assert calls == []


async def test_callbacks_registered_before_the_session_began_run_at_the_first_commit(
    db_session: AppSession,
) -> None:
    calls: list[str] = []
    after_commit(db_session, lambda: calls.append("early"))
    await db_session.commit()
    assert calls == ["early"]
    await db_session.commit()
    assert calls == ["early"]  # run once


def _session(engine: AsyncEngine) -> AppSession:
    return async_sessionmaker(engine, class_=AppSession, expire_on_commit=False)()


async def test_callbacks_parked_by_a_commit_that_bypassed_session_commit_are_dropped(
    db_engine: AsyncEngine, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[str] = []
    async with _session(db_engine) as session:
        async with session.begin():  # bypasses AppSession.commit
            await session.execute(text("select 1"))
            after_commit(session, lambda: calls.append("bypassed"))
        with caplog.at_level(logging.WARNING, logger="app.core.db"):
            await session.execute(text("select 1"))  # next root transaction starts
            after_commit(session, lambda: calls.append("next"))
            await session.commit()
    assert calls == ["next"]
    assert any("did not go through session.commit()" in r.getMessage() for r in caplog.records)


async def test_callbacks_parked_by_a_bypassing_commit_are_logged_and_dropped_on_close(
    db_engine: AsyncEngine, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[str] = []
    session = _session(db_engine)
    async with session.begin():  # bypasses AppSession.commit
        await session.execute(text("select 1"))
        after_commit(session, lambda: calls.append("bypassed"))
    with caplog.at_level(logging.WARNING, logger="app.core.db"):
        await session.close()
    assert calls == []
    assert any("commit bypassed AppSession.commit" in r.getMessage() for r in caplog.records)


async def test_rolled_back_callbacks_are_dropped_on_close_without_a_warning(
    db_engine: AsyncEngine, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[str] = []
    session = _session(db_engine)
    await session.execute(text("select 1"))
    after_commit(session, lambda: calls.append("rolled back"))
    with caplog.at_level(logging.WARNING, logger="app.core.db"):
        await session.rollback()
        await session.close()
    assert calls == []
    assert not [r for r in caplog.records if "dropped" in r.getMessage()]
