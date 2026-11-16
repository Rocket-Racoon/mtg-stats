"""Concurrent registrations with real commits on a throwaway database (S-2.1.3 AC4, AC5).

The shared test session runs inside a rolled-back transaction, so it cannot contend for a row
lock. These tests migrate a throwaway ``mtg_stats_mig_*`` database to head, point the real app at
it and prove the second request waited by watching ``pg_locks`` for an ungranted lock.
"""

import asyncio
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import db
from app.main import create_app
from app.playgroups.services import hash_invite_token, lock_invite
from tests.invite_support import make_invite
from tests.migration_support import alembic_config, temporary_database
from tests.register_support import assert_envelope, make_inviter, new_token, register

WAIT_SECONDS = 20.0
ROW_WAIT_TYPES = {"transactionid", "tuple"}
"""Lock types a blocked ``SELECT ... FOR UPDATE`` shows while waiting for a row holder."""


@dataclass
class Live:
    client: httpx.AsyncClient
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
    """The real app (real commits) on the throwaway database, plus a probe/locker engine."""
    app = create_app()
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    probe = create_async_engine(migrated_url, poolclass=NullPool)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            yield Live(client=client, engine=probe)
    finally:
        await probe.dispose()
        await db.dispose_engine()


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


async def _new_invite(live: Live, **fields: Any) -> str:
    """Commit an inviter and an invite with a known token; return the token."""
    token = new_token()
    async with db.get_sessionmaker()() as session:
        inviter = await make_inviter(session)
        await make_invite(session, inviter, token_hash=hash_invite_token(token), **fields)
    return token


async def _scalar(live: Live, sql: str, **params: Any) -> Any:
    async with live.engine.connect() as conn:
        return (await conn.execute(text(sql), params)).scalar_one()


async def _use_count(live: Live, token: str) -> int:
    return int(
        await _scalar(
            live, "SELECT use_count FROM invites WHERE token_hash = :h", h=hash_invite_token(token)
        )
    )


async def _users_named(live: Live, *names: str) -> int:
    return int(
        await _scalar(live, "SELECT count(*) FROM users WHERE username = ANY(:n)", n=list(names))
    )


async def _hold_users_table(live: Live) -> AsyncConnection:
    """Open a transaction that blocks every INSERT into ``users`` (SELECTs still work)."""
    conn = await live.engine.connect()
    await conn.execute(text("LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE"))
    return conn


async def _release(conn: AsyncConnection) -> None:
    await conn.rollback()
    await conn.close()


# ------------------------------------------------------------------- AC4


async def test_a_registration_waits_while_another_transaction_holds_the_invite_lock(
    live: Live,
) -> None:
    token = await _new_invite(live)
    holder = db.get_sessionmaker()()
    task: asyncio.Task[httpx.Response] | None = None
    try:
        await lock_invite(holder, token)  # FOR UPDATE, left open
        task = asyncio.create_task(register(live.client, token, username="late.comer"))

        await _wait_until(live.engine, lambda types: bool(ROW_WAIT_TYPES & set(types)))
        await asyncio.sleep(0.3)
        assert not task.done()  # it is parked on the row lock, not failing fast

        await holder.execute(text("UPDATE invites SET use_count = max_uses"))
        await holder.commit()
        response = await asyncio.wait_for(task, WAIT_SECONDS)
    finally:
        await holder.close()
        if task is not None and not task.done():
            task.cancel()

    assert_envelope(response, 410, "invite_no_longer_valid")
    assert await _users_named(live, "late.comer") == 0
    assert await _use_count(live, token) == 1


async def test_a_waiting_registration_succeeds_when_the_lock_holder_rolls_back(
    live: Live,
) -> None:
    token = await _new_invite(live)
    holder = db.get_sessionmaker()()
    task: asyncio.Task[httpx.Response] | None = None
    try:
        await lock_invite(holder, token)
        task = asyncio.create_task(register(live.client, token, username="patient"))
        await _wait_until(live.engine, lambda types: bool(ROW_WAIT_TYPES & set(types)))
        await holder.rollback()
        response = await asyncio.wait_for(task, WAIT_SECONDS)
    finally:
        await holder.close()
        if task is not None and not task.done():
            task.cancel()

    assert response.status_code == 201
    assert await _use_count(live, token) == 1


async def test_two_simultaneous_registrations_on_a_single_use_invite_give_one_201_and_one_410(
    live: Live,
) -> None:
    token = await _new_invite(live)
    blocker = await _hold_users_table(live)  # parks the winner after it locked the invite
    tasks: list[asyncio.Task[httpx.Response]] = []
    try:
        tasks = [
            asyncio.create_task(register(live.client, token, username=name))
            for name in ("racer.one", "racer.two")
        ]
        # One request waits for the users table (it holds the invite row); the other waits for
        # that invite row: proof the second registration queued on the lock.
        types = await _wait_until(
            live.engine,
            lambda found: "relation" in found and bool(ROW_WAIT_TYPES & set(found)),
        )
        assert "relation" in types
        assert not any(task.done() for task in tasks)
    finally:
        await _release(blocker)
    responses = await asyncio.wait_for(asyncio.gather(*tasks), WAIT_SECONDS)

    statuses = sorted(r.status_code for r in responses)
    assert statuses == [201, 410]
    loser = next(r for r in responses if r.status_code == 410)
    assert_envelope(loser, 410, "invite_no_longer_valid")
    assert await _use_count(live, token) == 1
    assert await _users_named(live, "racer.one", "racer.two") == 1
    winner_name = await _scalar(
        live, "SELECT username FROM users WHERE username = ANY(:n)", n=["racer.one", "racer.two"]
    )
    assert (
        await _scalar(
            live,
            "SELECT count(*) FROM players p JOIN users u ON u.id = p.user_id WHERE u.username = :n",
            n=winner_name,
        )
        == 1
    )


async def test_many_simultaneous_registrations_on_a_single_use_invite_admit_exactly_one(
    live: Live,
) -> None:
    token = await _new_invite(live)
    names = [f"crowd.{i}" for i in range(6)]

    responses = await asyncio.wait_for(
        asyncio.gather(*(register(live.client, token, username=n) for n in names)), WAIT_SECONDS
    )

    assert sorted(r.status_code for r in responses) == [201] + [410] * 5
    assert await _use_count(live, token) == 1
    assert await _users_named(live, *names) == 1


async def test_a_two_use_invite_admits_exactly_two_of_four_simultaneous_registrations(
    live: Live,
) -> None:
    token = await _new_invite(live, max_uses=2)
    names = [f"duo.{i}" for i in range(4)]

    responses = await asyncio.wait_for(
        asyncio.gather(*(register(live.client, token, username=n) for n in names)), WAIT_SECONDS
    )

    assert sorted(r.status_code for r in responses) == [201, 201, 410, 410]
    assert await _use_count(live, token) == 2
    assert await _users_named(live, *names) == 2


# ------------------------------------------------------------------- AC5 under contention


async def test_the_same_username_on_two_invites_at_once_gives_one_201_and_one_409(
    live: Live,
) -> None:
    first = await _new_invite(live)
    second = await _new_invite(live)
    blocker = await _hold_users_table(live)  # both pass the username pre-check, then queue
    tasks: list[asyncio.Task[httpx.Response]] = []
    try:
        tasks = [
            asyncio.create_task(register(live.client, token, username=name))
            for token, name in ((first, "Same.Name"), (second, "same.name"))
        ]
        await _wait_until(live.engine, lambda found: found.count("relation") >= 2)
    finally:
        await _release(blocker)
    responses = await asyncio.wait_for(asyncio.gather(*tasks), WAIT_SECONDS)

    assert sorted(r.status_code for r in responses) == [201, 409]
    loser_index = next(i for i, r in enumerate(responses) if r.status_code == 409)
    assert_envelope(responses[loser_index], 409, "username_taken")
    losing_token, winning_token = (first, second) if loser_index == 0 else (second, first)
    assert await _use_count(live, losing_token) == 0
    assert await _use_count(live, winning_token) == 1
    assert (
        await _scalar(live, "SELECT count(*) FROM users WHERE lower(username) = 'same.name'") == 1
    )
    assert (
        await _scalar(
            live,
            "SELECT count(*) FROM players p JOIN users u ON u.id = p.user_id "
            "WHERE lower(u.username) = 'same.name'",
        )
        == 1
    )


async def test_the_loser_of_a_username_race_can_reuse_its_invite_with_another_name(
    live: Live,
) -> None:
    first = await _new_invite(live)
    second = await _new_invite(live)
    blocker = await _hold_users_table(live)
    tasks: list[asyncio.Task[httpx.Response]] = []
    try:
        tasks = [
            asyncio.create_task(register(live.client, token, username="clash"))
            for token in (first, second)
        ]
        await _wait_until(live.engine, lambda found: found.count("relation") >= 2)
    finally:
        await _release(blocker)
    responses = await asyncio.wait_for(asyncio.gather(*tasks), WAIT_SECONDS)
    loser_token = first if responses[0].status_code == 409 else second

    retry = await register(live.client, loser_token, username="clash.two")

    assert retry.status_code == 201
    assert await _use_count(live, loser_token) == 1


async def test_the_throwaway_database_fixture_leaves_the_shared_database_untouched(
    live: Live,
) -> None:
    """Sanity: the app under test is on the throwaway DB, never ``mtg_stats*``."""
    name = await _scalar(live, "SELECT current_database()")
    assert name.startswith("mtg_stats_mig_")
