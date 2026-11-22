"""Concurrent joins count each use once, with real commits (S-2.4.2).

Like ``test_playgroups_ownership_concurrency.py`` this uses a throwaway migrated database. A
holder session takes the invite row's ``FOR UPDATE`` lock, the contenders queue behind it (seen
as ungranted locks in ``pg_locks``), then the holder releases and they run one after another,
each re-reading the invite the previous one committed.
"""

import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine, Iterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import db
from app.core.errors import DomainError
from app.identity.services import create_user_with_player
from app.playgroups import repository, services
from app.playgroups.models import Membership
from tests.migration_support import alembic_config, temporary_database

WAIT_SECONDS = 20.0
PASSWORD = "correct horse battery"  # secrets-check: allow


@dataclass
class Live:
    engine: AsyncEngine


@dataclass
class Seeded:
    users: list[UUID]
    tokens: list[str]
    invite_ids: list[UUID]


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
    probe = create_async_engine(migrated_url, poolclass=NullPool)
    try:
        yield Live(engine=probe)
    finally:
        await probe.dispose()
        await db.dispose_engine()


async def _wait_until_blocked(engine: AsyncEngine, waiters: int) -> None:
    async with asyncio.timeout(WAIT_SECONDS):
        while True:
            async with engine.connect() as conn:
                count = (
                    await conn.execute(
                        text(
                            "SELECT count(*) FROM pg_locks l JOIN pg_stat_activity a "
                            "ON a.pid = l.pid WHERE NOT l.granted "
                            "AND a.datname = current_database() AND a.pid <> pg_backend_pid()"
                        )
                    )
                ).scalar_one()
            if int(count) >= waiters:
                return
            await asyncio.sleep(0.05)


async def _seed(*, invites: int = 1, max_uses: int = 1, joiners: int = 2) -> Seeded:
    async with db.get_sessionmaker()() as session:
        accounts: list[UUID] = []
        for name in ["owner", *[f"joiner{i}" for i in range(joiners)]]:
            info = await create_user_with_player(
                session, username=name, password=PASSWORD, display_name=name.title()
            )
            assert info.user_id is not None
            accounts.append(info.user_id)
        group = await services.create_playgroup(session, user_id=accounts[0], name="Friday")
        access = await services.get_membership(session, playgroup_id=group.id, user_id=accounts[0])
        assert access is not None
        tokens: list[str] = []
        ids: list[UUID] = []
        for _ in range(invites):
            created = await services.create_playgroup_invite(session, access, max_uses=max_uses)
            tokens.append(created.url.rsplit("/", 1)[1])
            ids.append(created.invite.id)
        await session.commit()
    return Seeded(accounts[1:], tokens, ids)


async def _join(user_id: UUID, token: str) -> bool:
    """Join in its own session and commit; return ``already_member``."""
    async with db.get_sessionmaker()() as session:
        try:
            result = await services.join_with_invite(session, user_id=user_id, token=token)
        except DomainError:
            await session.rollback()
            raise
        await session.commit()
        return result.already_member


async def _run_behind_invite_lock(
    live: Live, invite_id: UUID, *factories: Callable[[], Coroutine[Any, Any, bool]]
) -> list[bool | BaseException]:
    """Hold the invite lock, start every call (so each queues behind it), release, collect."""
    holder = db.get_sessionmaker()()
    tasks: list[asyncio.Task[bool]] = []
    try:
        await repository.get_invite_for_update(holder, invite_id)
        tasks = [asyncio.ensure_future(factory()) for factory in factories]
        await _wait_until_blocked(live.engine, waiters=len(tasks))
        await asyncio.sleep(0.3)
        assert not any(task.done() for task in tasks)  # parked on the lock, not failing fast
        await holder.rollback()
        return list(
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), WAIT_SECONDS)
        )
    finally:
        await holder.close()
        for task in tasks:
            if not task.done():
                task.cancel()


async def _scalar(live: Live, sql: str, **params: Any) -> Any:
    async with live.engine.connect() as conn:
        return (await conn.execute(text(sql), params)).scalar_one()


async def test_two_users_racing_for_a_single_use_invite_admit_exactly_one(live: Live) -> None:
    seed = await _seed()
    outcomes = await _run_behind_invite_lock(
        live,
        seed.invite_ids[0],
        lambda: _join(seed.users[0], seed.tokens[0]),
        lambda: _join(seed.users[1], seed.tokens[0]),
    )
    winners = [o for o in outcomes if o is False]
    losers = [o for o in outcomes if isinstance(o, DomainError)]
    assert len(winners) == 1
    assert len(losers) == 1
    assert losers[0].status_code == 410
    assert losers[0].code == "invite_no_longer_valid"
    assert await _scalar(live, "SELECT use_count FROM invites") == 1
    assert await _scalar(live, "SELECT count(*) FROM memberships WHERE role = 'member'") == 1


async def test_one_user_joining_twice_at_once_is_counted_once(live: Live) -> None:
    seed = await _seed(max_uses=5)
    outcomes = await _run_behind_invite_lock(
        live,
        seed.invite_ids[0],
        lambda: _join(seed.users[0], seed.tokens[0]),
        lambda: _join(seed.users[0], seed.tokens[0]),
    )
    assert sorted(str(o) for o in outcomes) == ["False", "True"]  # joined, then "already in"
    assert await _scalar(live, "SELECT use_count FROM invites") == 1
    assert await _scalar(live, "SELECT count(*) FROM memberships WHERE role = 'member'") == 1


async def test_a_join_losing_the_unique_index_race_rolls_back_its_use(live: Live) -> None:
    """Deterministic: another transaction (a join through a second invite, say) has inserted the
    user's membership but not committed. Our join passes the member check, takes the invite
    lock, and blocks on the unique index; when the other commits it gets an IntegrityError,
    rolls back its savepoint (and its use) and answers ``already_member``."""
    seed = await _seed(max_uses=5, joiners=1)
    async with db.get_sessionmaker()() as holder:
        group_id = await _scalar(live, "SELECT playgroup_id FROM invites")
        player_id = await _scalar(
            live, "SELECT id FROM players WHERE user_id = :u", u=seed.users[0]
        )
        repository.add_membership(
            holder, Membership(playgroup_id=group_id, player_id=player_id, role="member")
        )
        await holder.flush()  # inserted, uncommitted: holds the unique index entry
        task = asyncio.ensure_future(_join(seed.users[0], seed.tokens[0]))
        try:
            await _wait_until_blocked(live.engine, waiters=1)
            await asyncio.sleep(0.3)
            assert not task.done()
            await holder.commit()
            assert await asyncio.wait_for(task, WAIT_SECONDS) is True
        finally:
            if not task.done():
                task.cancel()
    assert await _scalar(live, "SELECT use_count FROM invites") == 0
    assert await _scalar(live, "SELECT count(*) FROM memberships WHERE role = 'member'") == 1


async def test_a_double_tap_on_a_single_use_invite_answers_already_member_to_the_second(
    live: Live,
) -> None:
    seed = await _seed(max_uses=1, joiners=1)
    outcomes = await _run_behind_invite_lock(
        live,
        seed.invite_ids[0],
        lambda: _join(seed.users[0], seed.tokens[0]),
        lambda: _join(seed.users[0], seed.tokens[0]),
    )
    assert sorted(str(o) for o in outcomes) == ["False", "True"]  # joined, then "already in"
    assert await _scalar(live, "SELECT use_count FROM invites") == 1
    assert await _scalar(live, "SELECT count(*) FROM memberships WHERE role = 'member'") == 1
