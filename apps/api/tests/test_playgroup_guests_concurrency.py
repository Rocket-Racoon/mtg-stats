"""Concurrent guest adds and renames in one playgroup keep names unique, with real commits.

Like ``test_playgroups_ownership_concurrency.py``: a throwaway migrated database; a holder session
takes the playgroup's ``FOR UPDATE`` lock, the calls queue behind it (ungranted locks in
``pg_locks``), then the holder releases and they run one after the other (S-2.3.1).
"""

import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine, Iterator
from typing import Any
from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import db
from app.core.config import get_settings
from app.core.errors import DomainError
from app.identity.services import create_user_with_player
from app.playgroups import repository, services
from tests.invite_support import token_from_url
from tests.migration_support import alembic_config, temporary_database

WAIT_SECONDS = 20.0
PASSWORD = "correct horse battery"  # secrets-check: allow


@pytest.fixture
def migrated_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A throwaway database at head; always dropped."""
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
async def engine(migrated_url: str) -> AsyncIterator[AsyncEngine]:
    probe = create_async_engine(migrated_url, poolclass=NullPool)
    try:
        yield probe
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


async def _seed() -> tuple[UUID, UUID]:
    """A playgroup and its owner's user id."""
    async with db.get_sessionmaker()() as session:
        info = await create_user_with_player(
            session, username="owner", password=PASSWORD, display_name="Owner"
        )
        assert info.user_id is not None
        group = await services.create_playgroup(session, user_id=info.user_id, name="Friday Night")
        await session.commit()
    return group.id, info.user_id


async def _call(
    group_id: UUID, user_id: UUID, action: Callable[..., Coroutine[Any, Any, object]], **kwargs: Any
) -> None:
    async with db.get_sessionmaker()() as session:
        access = await services.get_membership(session, playgroup_id=group_id, user_id=user_id)
        assert access is not None
        try:
            await action(session, access, **kwargs)
        except DomainError:
            await session.rollback()
            raise
        await session.commit()


async def _run_behind_lock(
    engine: AsyncEngine, group_id: UUID, *factories: Callable[[], Coroutine[Any, Any, None]]
) -> list[BaseException | None]:
    holder = db.get_sessionmaker()()
    tasks: list[asyncio.Task[None]] = []
    try:
        await repository.lock_playgroup(holder, group_id)
        tasks = [asyncio.ensure_future(factory()) for factory in factories]
        await _wait_until_blocked(engine, waiters=len(tasks))
        await asyncio.sleep(0.3)
        assert not any(task.done() for task in tasks)
        await holder.rollback()
        return list(
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), WAIT_SECONDS)
        )
    finally:
        await holder.close()
        for task in tasks:
            if not task.done():
                task.cancel()


async def _guest_names(engine: AsyncEngine, group_id: UUID) -> list[str]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT display_name FROM players WHERE home_playgroup_id = :g"), {"g": group_id}
        )
        return [row.display_name for row in rows]


async def test_two_concurrent_adds_of_one_name_create_exactly_one_guest(
    engine: AsyncEngine,
) -> None:
    group_id, user_id = await _seed()
    outcomes = await _run_behind_lock(
        engine,
        group_id,
        lambda: _call(group_id, user_id, services.add_guest, display_name="Zoë"),
        lambda: _call(group_id, user_id, services.add_guest, display_name="ZOE"),
    )
    losers = [o for o in outcomes if o is not None]
    assert len(losers) == 1
    assert isinstance(losers[0], services.PlayerNameTakenError)
    assert len(await _guest_names(engine, group_id)) == 1


async def test_two_guests_renamed_to_one_name_concurrently_leave_one_holder(
    engine: AsyncEngine,
) -> None:
    group_id, user_id = await _seed()
    async with db.get_sessionmaker()() as session:
        access = await services.get_membership(session, playgroup_id=group_id, user_id=user_id)
        assert access is not None
        first = await services.add_guest(session, access, display_name="A")
        second = await services.add_guest(session, access, display_name="B")
        await session.commit()
    outcomes = await _run_behind_lock(
        engine,
        group_id,
        lambda: _call(
            group_id, user_id, services.rename_guest, player_id=first.player_id, display_name="Cy"
        ),
        lambda: _call(
            group_id, user_id, services.rename_guest, player_id=second.player_id, display_name="CY"
        ),
    )
    assert len([o for o in outcomes if o is not None]) == 1
    names = await _guest_names(engine, group_id)
    assert len(names) == 2
    assert [n.casefold() for n in names].count("cy") == 1


# --- the lock really blocks joins (review follow-up) ------------------------------------------


async def _seed_invite_and_joiner(
    group_id: UUID, owner_user: UUID, joiner_name: str
) -> tuple[UUID, str]:
    """A second account named ``joiner_name`` and a playgroup invite; return (user id, token)."""
    async with db.get_sessionmaker()() as session:
        joiner = await create_user_with_player(
            session,
            username=joiner_name.lower() + "_user",
            password=PASSWORD,
            display_name=joiner_name,
        )
        access = await services.get_membership(session, playgroup_id=group_id, user_id=owner_user)
        assert access is not None
        created = await services.create_playgroup_invite(session, access)
        await session.commit()
    assert joiner.user_id is not None
    return joiner.user_id, token_from_url(created.url, get_settings().public_web_url)


async def _member_count(engine: AsyncEngine, group_id: UUID) -> int:
    async with engine.connect() as conn:
        return int(
            (
                await conn.execute(
                    text("SELECT count(*) FROM memberships WHERE playgroup_id = :g"),
                    {"g": group_id},
                )
            ).scalar_one()
        )


async def _join(user_id: UUID, token: str) -> None:
    async with db.get_sessionmaker()() as session:
        await services.join_with_invite(session, user_id=user_id, token=token)
        await session.commit()


async def test_a_join_waits_behind_the_playgroup_lock_and_completes_after_release(
    engine: AsyncEngine,
) -> None:
    group_id, owner_user = await _seed()
    joiner_user, token = await _seed_invite_and_joiner(group_id, owner_user, "Joiner")
    outcomes = await _run_behind_lock(engine, group_id, lambda: _join(joiner_user, token))
    assert outcomes == [None]
    assert await _member_count(engine, group_id) == 2


async def test_a_guest_add_waits_for_an_uncommitted_join_and_then_sees_the_member_name(
    engine: AsyncEngine,
) -> None:
    group_id, owner_user = await _seed()
    joiner_user, token = await _seed_invite_and_joiner(group_id, owner_user, "Bob")
    joiner_session = db.get_sessionmaker()()
    add: asyncio.Task[None] | None = None
    try:
        await services.join_with_invite(joiner_session, user_id=joiner_user, token=token)
        # flushed, not committed: the membership insert holds FOR KEY SHARE on the playgroup row
        add = asyncio.ensure_future(
            _call(group_id, owner_user, services.add_guest, display_name="bob")
        )
        await _wait_until_blocked(engine, waiters=1)
        await asyncio.sleep(0.3)
        assert not add.done()  # parked on the playgroup lock, not failing fast
        await joiner_session.commit()
        with pytest.raises(services.PlayerNameTakenError):
            await asyncio.wait_for(add, WAIT_SECONDS)
    finally:
        await joiner_session.close()
        if add is not None and not add.done():
            add.cancel()
    assert await _guest_names(engine, group_id) == []
