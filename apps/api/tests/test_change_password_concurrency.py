"""Password change and ``set_password`` against refreshes and sign-ins, with real commits (S-2.1.8).

Race under test: a refresh holds its refresh row's ``FOR UPDATE`` lock and inserts a replacement
row; a bulk revoke that runs meanwhile never sees the new row, so a token survives the password
change. Same for a sign-in that verified the OLD password. The fix orders locks user first, then
token. Like ``test_register_concurrency.py`` this uses a throwaway ``mtg_stats_mig_*`` database
and proves the second transaction waited by watching ``pg_locks`` for an ungranted lock.

Services are called directly with independent sessions (a real commit each), so each test decides
exactly when a transaction commits.
"""

import asyncio
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import db
from app.core.auth import InvalidTokenError
from app.identity.services import (
    InvalidCredentialsError,
    SignInResult,
    change_password,
    create_user_with_player,
    hash_refresh_token,
    refresh_session,
    set_password,
    sign_in,
    start_session,
)
from tests.migration_support import alembic_config, temporary_database

WAIT_SECONDS = 20.0
USERNAME = "racer"
OLD = "correct horse battery"  # secrets-check: allow
NEW = "a brand new passphrase"  # secrets-check: allow
IP = "10.1.2.3"


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


async def _waiting_lock_count(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT count(*) FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid "
                "WHERE NOT l.granted AND a.datname = current_database() "
                "AND a.pid <> pg_backend_pid()"
            )
        )
        return int(result.scalar_one())


async def _wait_until_blocked(engine: AsyncEngine) -> None:
    """Poll until some backend waits on an ungranted lock; fail after the timeout."""
    async with asyncio.timeout(WAIT_SECONDS):
        while True:
            if await _waiting_lock_count(engine) > 0:
                return
            await asyncio.sleep(0.05)


async def _assert_parked(engine: AsyncEngine, task: "asyncio.Task[Any]") -> None:
    await _wait_until_blocked(engine)
    await asyncio.sleep(0.3)
    assert not task.done()  # parked on a lock, not failing fast


async def _seed() -> tuple[UUID, list[SignInResult]]:
    """Commit a user with two signed-in sessions; return the user id and the two results."""
    async with db.get_sessionmaker()() as session:
        info = await create_user_with_player(
            session, username=USERNAME, password=OLD, display_name="Racer"
        )
        first = await start_session(session, user_id=info.user_id)
        second = await start_session(session, user_id=info.user_id)
        await session.commit()
    return info.user_id, [first, second]


async def _live_hashes(live: Live, user_id: UUID) -> set[str]:
    async with live.engine.connect() as conn:
        result = await conn.execute(
            text("SELECT token_hash FROM refresh_tokens WHERE user_id = :u AND revoked_at IS NULL"),
            {"u": user_id},
        )
        return {str(row[0]) for row in result}


async def _password_hash(live: Live, user_id: UUID) -> str:
    async with live.engine.connect() as conn:
        result = await conn.execute(
            text("SELECT password_hash FROM users WHERE id = :u"), {"u": user_id}
        )
        return str(result.scalar_one())


async def _refresh(token: str) -> SignInResult | BaseException:
    """Run ``refresh_session`` in its own session and commit, as the request session does."""
    async with db.get_sessionmaker()() as session:
        try:
            result = await refresh_session(session, refresh_token=token)
        except InvalidTokenError as exc:
            await session.commit()  # RefreshTokenReuseError persists its revocation
            return exc
        await session.commit()
        return result


async def _sign_in(password: str) -> SignInResult | BaseException:
    async with db.get_sessionmaker()() as session:
        try:
            result = await sign_in(session, username=USERNAME, password=password)
        except InvalidCredentialsError as exc:
            await session.rollback()
            return exc
        await session.commit()
        return result


async def _change(user_id: UUID) -> SignInResult:
    async with db.get_sessionmaker()() as session:
        result = await change_password(
            session, user_id=user_id, current_password=OLD, new_password=NEW, ip=IP
        )
        await session.commit()
        return result


async def _assert_cannot_refresh(token: str) -> None:
    outcome = await _refresh(token)
    assert isinstance(outcome, InvalidTokenError)


# ------------------------------------------------------------------- (a) refresh vs change


async def test_a_change_waits_for_an_open_refresh_and_then_revokes_the_token_it_created(
    live: Live,
) -> None:
    user_id, (first, other) = await _seed()
    refresher = db.get_sessionmaker()()
    task: asyncio.Task[SignInResult] | None = None
    try:
        rotated = await refresh_session(refresher, refresh_token=first.refresh_token.token)
        task = asyncio.create_task(_change(user_id))

        await _assert_parked(live.engine, task)

        await refresher.commit()
        changed = await asyncio.wait_for(task, WAIT_SECONDS)
    finally:
        await refresher.close()
        if task is not None and not task.done():
            task.cancel()

    # Every pre-existing token, including the one the refresh just created, is revoked.
    assert await _live_hashes(live, user_id) == {hash_refresh_token(changed.refresh_token.token)}
    await _assert_cannot_refresh(rotated.refresh_token.token)
    await _assert_cannot_refresh(other.refresh_token.token)


async def test_a_refresh_waits_for_an_open_change_and_then_fails_without_a_live_token(
    live: Live,
) -> None:
    user_id, (first, second) = await _seed()
    changer = db.get_sessionmaker()()
    task: asyncio.Task[SignInResult | BaseException] | None = None
    try:
        changed = await change_password(
            changer, user_id=user_id, current_password=OLD, new_password=NEW, ip=IP
        )
        task = asyncio.create_task(_refresh(second.refresh_token.token))

        await _assert_parked(live.engine, task)

        await changer.commit()
        outcome = await asyncio.wait_for(task, WAIT_SECONDS)
    finally:
        await changer.close()
        if task is not None and not task.done():
            task.cancel()

    assert isinstance(outcome, InvalidTokenError)
    assert await _live_hashes(live, user_id) == {hash_refresh_token(changed.refresh_token.token)}
    await _assert_cannot_refresh(first.refresh_token.token)


# ------------------------------------------------------------------- (b) old sign-in vs change


async def test_a_sign_in_with_the_old_password_waits_for_an_open_change_then_fails(
    live: Live,
) -> None:
    user_id, _ = await _seed()
    changer = db.get_sessionmaker()()
    task: asyncio.Task[SignInResult | BaseException] | None = None
    try:
        changed = await change_password(
            changer, user_id=user_id, current_password=OLD, new_password=NEW, ip=IP
        )
        task = asyncio.create_task(_sign_in(OLD))

        await _assert_parked(live.engine, task)

        await changer.commit()
        outcome = await asyncio.wait_for(task, WAIT_SECONDS)
    finally:
        await changer.close()
        if task is not None and not task.done():
            task.cancel()

    assert isinstance(outcome, InvalidCredentialsError)
    assert await _live_hashes(live, user_id) == {hash_refresh_token(changed.refresh_token.token)}
    assert isinstance(await _sign_in(NEW), SignInResult)


# ------------------------------------------------------------------- (c) set_password vs refresh


async def test_set_password_waits_for_an_open_refresh_and_leaves_no_live_token(
    live: Live,
) -> None:
    user_id, (first, _other) = await _seed()
    refresher = db.get_sessionmaker()()
    task: asyncio.Task[None] | None = None

    async def run_set_password() -> None:
        async with db.get_sessionmaker()() as session:
            await set_password(session, username=USERNAME, password=NEW)
            await session.commit()

    old_hash = await _password_hash(live, user_id)
    try:
        rotated = await refresh_session(refresher, refresh_token=first.refresh_token.token)
        task = asyncio.create_task(run_set_password())

        await _assert_parked(live.engine, task)

        await refresher.commit()
        await asyncio.wait_for(task, WAIT_SECONDS)
    finally:
        await refresher.close()
        if task is not None and not task.done():
            task.cancel()

    assert await _password_hash(live, user_id) != old_hash
    assert await _live_hashes(live, user_id) == set()
    await _assert_cannot_refresh(rotated.refresh_token.token)


async def test_a_refresh_waits_for_an_open_set_password_and_leaves_no_live_token(
    live: Live,
) -> None:
    user_id, (first, _other) = await _seed()
    setter = db.get_sessionmaker()()
    task: asyncio.Task[SignInResult | BaseException] | None = None
    try:
        await set_password(setter, username=USERNAME, password=NEW)
        task = asyncio.create_task(_refresh(first.refresh_token.token))

        await _assert_parked(live.engine, task)

        await setter.commit()
        outcome = await asyncio.wait_for(task, WAIT_SECONDS)
    finally:
        await setter.close()
        if task is not None and not task.done():
            task.cancel()

    assert isinstance(outcome, InvalidTokenError)
    assert await _live_hashes(live, user_id) == set()
