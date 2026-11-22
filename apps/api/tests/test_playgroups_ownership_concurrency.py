"""Concurrent transfers and role changes keep exactly one owner, with real commits (S-2.4.3).

Like ``test_change_password_concurrency.py`` this uses a throwaway migrated database. A holder
session takes the memberships' ``FOR UPDATE`` lock first, two service calls queue behind it (seen
as ungranted locks in ``pg_locks``), then the holder releases and the calls run one after the
other, each re-reading the roles the previous one committed.
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
from app.playgroups.models import ROLE_MEMBER, Membership
from tests.migration_support import alembic_config, temporary_database

WAIT_SECONDS = 20.0
PASSWORD = "correct horse battery"  # secrets-check: allow


@dataclass
class Live:
    engine: AsyncEngine


@dataclass
class Seeded:
    group_id: UUID
    owner_user: UUID
    owner_player: UUID
    first_player: UUID
    second_player: UUID


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


async def _seed() -> Seeded:
    async with db.get_sessionmaker()() as session:
        players: list[tuple[UUID, UUID]] = []
        for name in ("owner", "first", "second"):
            info = await create_user_with_player(
                session, username=name, password=PASSWORD, display_name=name.title()
            )
            assert info.user_id is not None
            players.append((info.user_id, info.id))
        group = await services.create_playgroup(session, user_id=players[0][0], name="Friday Night")
        for _, player_id in players[1:]:
            repository.add_membership(
                session,
                Membership(playgroup_id=group.id, player_id=player_id, role=ROLE_MEMBER),
            )
        await session.commit()
    return Seeded(group.id, players[0][0], players[0][1], players[1][1], players[2][1])


async def _transfer(seed: Seeded, target: UUID) -> None:
    async with db.get_sessionmaker()() as session:
        access = await services.get_membership(
            session, playgroup_id=seed.group_id, user_id=seed.owner_user
        )
        assert access is not None  # taken before the lock wait: the snapshot may go stale
        try:
            await services.transfer_ownership(session, access, player_id=target)
        except DomainError:
            await session.rollback()
            raise
        await session.commit()


async def _change_role(seed: Seeded, target: UUID, role: str) -> None:
    async with db.get_sessionmaker()() as session:
        access = await services.get_membership(
            session, playgroup_id=seed.group_id, user_id=seed.owner_user
        )
        assert access is not None
        try:
            await services.change_member_role(session, access, player_id=target, role=role)
        except DomainError:
            await session.rollback()
            raise
        await session.commit()


async def _roles(live: Live, seed: Seeded) -> dict[UUID, str]:
    async with live.engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT player_id, role FROM memberships "
                "WHERE playgroup_id = :g AND left_at IS NULL"
            ),
            {"g": seed.group_id},
        )
        return {row.player_id: row.role for row in rows}


async def _run_behind_lock(
    live: Live, seed: Seeded, *factories: Callable[[], Coroutine[Any, Any, None]]
) -> list[BaseException | None]:
    """Hold the lock, start every call (so each queues behind it), release, collect outcomes."""
    holder = db.get_sessionmaker()()
    tasks: list[asyncio.Task[None]] = []
    try:
        await repository.lock_active_memberships(holder, seed.group_id)
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


def _is_403(outcome: BaseException | None) -> bool:
    return isinstance(outcome, DomainError) and outcome.status_code == 403


async def test_two_concurrent_transfers_leave_exactly_one_owner(live: Live) -> None:
    seed = await _seed()
    outcomes = await _run_behind_lock(
        live,
        seed,
        lambda: _transfer(seed, seed.first_player),
        lambda: _transfer(seed, seed.second_player),
    )
    winners = [o for o in outcomes if o is None]
    losers = [o for o in outcomes if o is not None]
    assert len(winners) == 1
    assert len(losers) == 1
    assert isinstance(losers[0], DomainError)  # the loser re-read the row: no longer the owner
    assert losers[0].status_code == 403
    roles = await _roles(live, seed)
    assert sorted(roles.values()) == ["admin", "member", "owner"]
    assert roles[seed.owner_player] == "admin"


async def test_a_transfer_racing_a_role_change_never_leaves_zero_or_two_owners(
    live: Live,
) -> None:
    seed = await _seed()
    outcomes = await _run_behind_lock(
        live,
        seed,
        lambda: _transfer(seed, seed.first_player),
        lambda: _change_role(seed, seed.second_player, "admin"),
    )
    # Whichever ran first, the owner's own request is authorised by the locked row: the role
    # change either ran before the transfer (both succeed) or after it (403). Nothing else,
    # in particular no IntegrityError.
    transfer_outcome, change_outcome = outcomes
    assert transfer_outcome is None  # a role change of someone else never blocks a transfer
    assert change_outcome is None or _is_403(change_outcome)
    roles = await _roles(live, seed)
    assert roles[seed.second_player] == ("admin" if change_outcome is None else "member")
    assert list(roles.values()).count("owner") == 1
    assert roles[seed.first_player] == "owner"
    assert len(roles) == 3
