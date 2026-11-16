"""Concurrent revokes of the last two site admins, with real commits (S-2.1.11 decision 2).

Two admins are revoked at the same time, each by its own transaction. The role must never reach
zero active admins: exactly one revoke succeeds and the other gets ``LastSiteAdminError``. Like
``test_register_concurrency.py`` this uses a throwaway ``mtg_stats_mig_*`` database, and proves the
second transaction waited on the row lock by watching ``pg_locks`` for an ungranted lock.
"""

import asyncio
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import db
from app.identity.services import LastSiteAdminError, SiteAdminChange, set_site_admin
from tests.factories import UserFactory, create
from tests.migration_support import alembic_config, temporary_database

WAIT_SECONDS = 20.0
ROW_WAIT_TYPES = {"transactionid", "tuple"}
"""Lock types a blocked ``SELECT ... FOR UPDATE`` shows while waiting for a row holder."""


@dataclass
class Live:
    engine: AsyncEngine


@pytest.fixture
def migrated_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A throwaway database at head; always dropped (``temporary_database`` guarantees it)."""
    with temporary_database(monkeypatch) as url:
        db.get_sessionmaker.cache_clear()
        db.get_engine.cache_clear()
        command.upgrade(alembic_config(), "head")
        try:
            yield url
        finally:
            db.get_sessionmaker.cache_clear()
            db.get_engine.cache_clear()


@pytest.fixture
async def live(migrated_url: str) -> AsyncIterator[Live]:
    """A probe engine on the throwaway database; the app's own engine points at it too."""
    probe = create_async_engine(migrated_url, poolclass=NullPool)
    try:
        yield Live(engine=probe)
    finally:
        await probe.dispose()
        await db.dispose_engine()


async def _seed(*admins: tuple[str, bool, bool]) -> None:
    """Commit users given as ``(username, is_site_admin, is_active)``."""
    async with db.get_sessionmaker()() as session:
        for username, is_admin, active in admins:
            await create(
                session, UserFactory, username=username, is_site_admin=is_admin, is_active=active
            )
        await session.commit()


async def _revoke_and_commit(username: str) -> SiteAdminChange:
    return await _set_and_commit(username, False)


async def _set_and_commit(username: str, value: bool) -> SiteAdminChange:
    """What the CLI does: one session, ``set_site_admin``, commit; roll back on any error."""
    async with db.get_sessionmaker()() as session:
        try:
            change = await set_site_admin(session, username=username, value=value)
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
        return change


async def _active_admins(live: Live) -> list[str]:
    async with live.engine.connect() as conn:
        result = await conn.execute(
            text("SELECT username FROM users WHERE is_site_admin AND is_active ORDER BY username")
        )
        return [str(r[0]) for r in result]


async def _waiting_lock_types(engine: AsyncEngine) -> list[str]:
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT l.locktype FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid "
                "WHERE NOT l.granted AND a.datname = current_database() "
                "AND a.pid <> pg_backend_pid()"
            )
        )
        return [str(row[0]) for row in result]


async def _wait_until(engine: AsyncEngine, predicate: Any) -> list[str]:
    """Poll the ungranted locks until ``predicate(types)`` holds; fail after the timeout."""
    types: list[str] = []
    async with asyncio.timeout(WAIT_SECONDS):
        while True:
            types = await _waiting_lock_types(engine)
            if predicate(types):
                return types
            await asyncio.sleep(0.05)


async def test_a_revoke_waits_while_another_revoke_holds_the_admin_row_locks(live: Live) -> None:
    await _seed(("alpha", True, True), ("bravo", True, True))
    holder = db.get_sessionmaker()()
    task: asyncio.Task[SiteAdminChange] | None = None
    try:
        # The first transaction revokes alpha and stays open, holding its locks.
        first = await set_site_admin(holder, username="alpha", value=False)
        assert (first.old, first.new) == (True, False)

        task = asyncio.create_task(_revoke_and_commit("bravo"))
        await _wait_until(live.engine, lambda types: bool(ROW_WAIT_TYPES & set(types)))
        await asyncio.sleep(0.3)
        assert not task.done()  # parked on the lock, not failing fast on a stale count

        await holder.commit()
        with pytest.raises(LastSiteAdminError):
            await asyncio.wait_for(task, WAIT_SECONDS)
    finally:
        await holder.close()
        if task is not None and not task.done():
            task.cancel()

    assert await _active_admins(live) == ["bravo"]


async def test_a_waiting_revoke_goes_through_when_the_lock_holder_rolls_back(live: Live) -> None:
    await _seed(("alpha", True, True), ("bravo", True, True))
    holder = db.get_sessionmaker()()
    task: asyncio.Task[SiteAdminChange] | None = None
    try:
        await set_site_admin(holder, username="alpha", value=False)
        task = asyncio.create_task(_revoke_and_commit("bravo"))
        await _wait_until(live.engine, lambda types: bool(ROW_WAIT_TYPES & set(types)))
        await holder.rollback()
        change = await asyncio.wait_for(task, WAIT_SECONDS)
    finally:
        await holder.close()
        if task is not None and not task.done():
            task.cancel()

    assert (change.old, change.new) == (True, False)
    assert await _active_admins(live) == ["alpha"]


async def test_two_simultaneous_revokes_of_the_last_two_admins_leave_exactly_one(
    live: Live,
) -> None:
    await _seed(("alpha", True, True), ("bravo", True, True))

    results = await asyncio.wait_for(
        asyncio.gather(
            _revoke_and_commit("alpha"), _revoke_and_commit("bravo"), return_exceptions=True
        ),
        WAIT_SECONDS,
    )

    succeeded = [r for r in results if isinstance(r, SiteAdminChange)]
    refused = [r for r in results if isinstance(r, LastSiteAdminError)]
    assert len(succeeded) == 1
    assert len(refused) == 1
    remaining = await _active_admins(live)
    assert len(remaining) == 1
    assert remaining == [next(n for n in ("alpha", "bravo") if n != succeeded[0].username)]


async def test_many_simultaneous_revokes_never_drop_below_one_active_admin(live: Live) -> None:
    names = [f"admin{i}" for i in range(4)]
    await _seed(*[(n, True, True) for n in names])

    results = await asyncio.wait_for(
        asyncio.gather(*(_revoke_and_commit(n) for n in names), return_exceptions=True),
        WAIT_SECONDS,
    )

    assert sum(isinstance(r, SiteAdminChange) for r in results) == 3
    assert sum(isinstance(r, LastSiteAdminError) for r in results) == 1
    assert len(await _active_admins(live)) == 1


async def test_an_inactive_admin_does_not_count_so_two_revokes_still_leave_one_active(
    live: Live,
) -> None:
    await _seed(("alpha", True, True), ("bravo", True, True), ("sleeper", True, False))

    results = await asyncio.wait_for(
        asyncio.gather(
            _revoke_and_commit("alpha"), _revoke_and_commit("bravo"), return_exceptions=True
        ),
        WAIT_SECONDS,
    )

    assert sum(isinstance(r, SiteAdminChange) for r in results) == 1
    assert sum(isinstance(r, LastSiteAdminError) for r in results) == 1
    assert len(await _active_admins(live)) == 1


async def test_a_grant_and_a_revoke_at_the_same_time_never_leave_zero_active_admins(
    live: Live,
) -> None:
    await _seed(("alpha", True, True), ("bravo", False, True))

    for _ in range(5):
        async with live.engine.begin() as conn:
            await conn.execute(text("UPDATE users SET is_site_admin = (username = 'alpha')"))

        results = await asyncio.wait_for(
            asyncio.gather(
                _set_and_commit("bravo", True),
                _set_and_commit("alpha", False),
                return_exceptions=True,
            ),
            WAIT_SECONDS,
        )

        grant, revoke = results
        assert isinstance(grant, SiteAdminChange)  # a grant never fails
        active = await _active_admins(live)
        assert active, "the role must never reach zero active admins"
        if isinstance(revoke, SiteAdminChange):
            assert active == ["bravo"]  # the grant ran first, so the revoke was allowed
        else:
            assert isinstance(revoke, LastSiteAdminError)
            assert active == ["alpha", "bravo"]  # the revoke ran first and was refused


async def test_revoking_one_of_several_inactive_admins_is_allowed_with_no_active_admin(
    live: Live,
) -> None:
    await _seed(("sleeper1", True, False), ("sleeper2", True, False))

    change = await _revoke_and_commit("sleeper1")

    assert (change.old, change.new) == (True, False)
    async with live.engine.connect() as conn:
        result = await conn.execute(text("SELECT username FROM users WHERE is_site_admin"))
        assert [r[0] for r in result] == ["sleeper2"]


async def test_the_throwaway_database_is_used_never_the_shared_one(live: Live) -> None:
    async with live.engine.connect() as conn:
        name = (await conn.execute(text("SELECT current_database()"))).scalar_one()
    assert name.startswith("mtg_stats_mig_")
