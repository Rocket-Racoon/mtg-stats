"""Refresh-token storage, daily cleanup and password-change revocation (S-2.1.5 AC5 and more)."""

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.core.db import AppSession
from app.identity import services
from app.identity.models import RefreshToken
from app.identity.services import (
    cleanup_refresh_tokens,
    hash_refresh_token,
    revoke_all_refresh_tokens,
    set_password,
)
from app.identity.services import jobs as jobs_module
from app.identity.services.jobs import cleanup_refresh_tokens_job
from app.workers.registry import JOBS
from app.workers.schedule import SCHEDULES, build_cron_jobs
from tests.factories import UserFactory, create
from tests.refresh_support import PASSWORD, RefreshTokenFactory, decode, make_user, rows

JOB = "identity.cleanup_refresh_tokens"
pytestmark = pytest.mark.usefixtures("app_schema")


# ------------------------------------------------------------------------------ storage


def test_hash_refresh_token_is_the_sha256_hex_of_the_token() -> None:
    assert hash_refresh_token("abc") == hashlib.sha256(b"abc").hexdigest()
    assert len(hash_refresh_token("abc")) == 64


async def test_sign_in_stores_the_hash_of_the_token_and_never_the_token(
    app_schema: AppSession,
) -> None:
    user = await make_user(app_schema)
    result = await services.sign_in(app_schema, username="ada", password=PASSWORD)
    token = result.refresh_token.token

    (row,) = await rows(app_schema)

    assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert row.user_id == user.id
    assert row.revoked_at is None
    assert row.replaced_by is None
    assert abs(row.expires_at - result.refresh_token.expires_at) < timedelta(seconds=1)
    assert decode(token)["exp"] == int(row.expires_at.timestamp())
    # the token (and its jti) appear in no column of the row
    mapper = inspect(RefreshToken)
    values = [str(getattr(row, column.key)) for column in mapper.columns]
    assert all(token not in value for value in values)
    assert all(result.refresh_token.jti not in value for value in values)


async def test_refresh_stores_only_the_hash_of_the_new_token(app_schema: AppSession) -> None:
    await make_user(app_schema)
    first = await services.sign_in(app_schema, username="ada", password=PASSWORD)
    second = await services.refresh_session(app_schema, refresh_token=first.refresh_token.token)
    hashes = {r.token_hash for r in await rows(app_schema)}
    assert hashes == {
        hash_refresh_token(first.refresh_token.token),
        hash_refresh_token(second.refresh_token.token),
    }


# ------------------------------------------------------------------------------ revoke_all


async def test_revoke_all_returns_how_many_live_tokens_it_revoked_for_that_user_only(
    app_schema: AppSession,
) -> None:
    ada = await create(app_schema, UserFactory)
    bob = await create(app_schema, UserFactory)
    future = datetime.now(UTC) + timedelta(days=1)
    for _ in range(2):
        await create(app_schema, RefreshTokenFactory, user_id=ada.id, expires_at=future)
    await create(app_schema, RefreshTokenFactory, user_id=bob.id, expires_at=future)

    assert await revoke_all_refresh_tokens(app_schema, user_id=ada.id) == 2

    assert all(r.revoked_at is not None for r in await rows(app_schema, user_id=ada.id))
    assert all(r.revoked_at is None for r in await rows(app_schema, user_id=bob.id))


# ------------------------------------------------------------------------------ set_password


async def test_set_password_revokes_all_of_the_users_refresh_tokens(
    app_schema: AppSession,
) -> None:
    ada = await make_user(app_schema, "ada")
    await make_user(app_schema, "bob")
    for _ in range(2):
        await services.sign_in(app_schema, username="ada", password=PASSWORD)
    await services.sign_in(app_schema, username="bob", password=PASSWORD)

    await set_password(app_schema, username="ada", password="a completely different one")

    assert all(r.revoked_at is not None for r in await rows(app_schema, user_id=ada.id))
    bob_rows = [r for r in await rows(app_schema) if r.user_id != ada.id]
    assert len(bob_rows) == 1
    assert bob_rows[0].revoked_at is None


async def test_a_token_issued_before_a_password_change_cannot_refresh(
    app_schema: AppSession,
) -> None:
    await make_user(app_schema)
    issued = await services.sign_in(app_schema, username="ada", password=PASSWORD)
    await set_password(app_schema, username="ada", password="a completely different one")
    with pytest.raises(services.RefreshTokenReuseError):
        await services.refresh_session(app_schema, refresh_token=issued.refresh_token.token)


# ------------------------------------------------------------------------------ cleanup (AC5)


async def _row_ids(session: AppSession) -> set[Any]:
    return {r.id for r in await rows(session)}


async def test_cleanup_deletes_rows_expired_more_than_seven_days_ago_and_returns_the_count(
    app_schema: AppSession,
) -> None:
    user = await create(app_schema, UserFactory)
    now = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
    old = [
        await create(
            app_schema,
            RefreshTokenFactory,
            user_id=user.id,
            expires_at=now - timedelta(days=7, seconds=1),
        ),
        await create(
            app_schema, RefreshTokenFactory, user_id=user.id, expires_at=now - timedelta(days=60)
        ),
        await create(
            app_schema,
            RefreshTokenFactory,
            user_id=user.id,
            expires_at=now - timedelta(days=8),
            revoked_at=now - timedelta(days=30),
        ),
    ]
    kept = [
        await create(
            app_schema, RefreshTokenFactory, user_id=user.id, expires_at=now - timedelta(days=6)
        ),
        await create(
            app_schema,
            RefreshTokenFactory,
            user_id=user.id,
            expires_at=now - timedelta(days=7),  # exactly 7 days: not "more than"
        ),
        await create(
            app_schema, RefreshTokenFactory, user_id=user.id, expires_at=now - timedelta(seconds=1)
        ),
        await create(
            app_schema, RefreshTokenFactory, user_id=user.id, expires_at=now + timedelta(days=1)
        ),
        await create(
            app_schema,
            RefreshTokenFactory,
            user_id=user.id,
            expires_at=now + timedelta(days=1),
            revoked_at=now - timedelta(hours=1),
        ),
    ]
    old_ids, kept_ids = {r.id for r in old}, {r.id for r in kept}

    deleted = await cleanup_refresh_tokens(app_schema, now=now)

    assert deleted == 3
    assert await _row_ids(app_schema) == kept_ids
    assert not old_ids & await _row_ids(app_schema)


async def test_cleanup_defaults_to_the_current_time(app_schema: AppSession) -> None:
    user = await create(app_schema, UserFactory)
    real_now = datetime.now(UTC)
    gone = await create(
        app_schema, RefreshTokenFactory, user_id=user.id, expires_at=real_now - timedelta(days=8)
    )
    kept = await create(
        app_schema, RefreshTokenFactory, user_id=user.id, expires_at=real_now - timedelta(days=6)
    )
    kept_id, gone_id = kept.id, gone.id
    assert await cleanup_refresh_tokens(app_schema) == 1
    assert await _row_ids(app_schema) == {kept_id}
    assert gone_id not in await _row_ids(app_schema)


async def test_cleanup_with_nothing_to_delete_returns_zero_and_is_idempotent(
    app_schema: AppSession,
) -> None:
    user = await create(app_schema, UserFactory)
    now = datetime.now(UTC)
    await create(
        app_schema, RefreshTokenFactory, user_id=user.id, expires_at=now - timedelta(days=9)
    )
    assert await cleanup_refresh_tokens(app_schema, now=now) == 1
    assert await cleanup_refresh_tokens(app_schema, now=now) == 0


async def test_cleanup_deleting_a_replacement_nulls_the_replaced_by_of_a_surviving_row(
    app_schema: AppSession,
) -> None:
    user = await create(app_schema, UserFactory)
    now = datetime.now(UTC)
    replacement = await create(
        app_schema, RefreshTokenFactory, user_id=user.id, expires_at=now - timedelta(days=8)
    )
    survivor = await create(
        app_schema,
        RefreshTokenFactory,
        user_id=user.id,
        expires_at=now + timedelta(days=1),
        revoked_at=now,
        replaced_by=replacement.id,
    )
    survivor_id = survivor.id

    assert await cleanup_refresh_tokens(app_schema, now=now) == 1

    (row,) = (
        await app_schema.execute(
            select(RefreshToken.id, RefreshToken.replaced_by)
            .where(RefreshToken.id == survivor_id)
            .execution_options(populate_existing=True)
        )
    ).all()
    assert row.replaced_by is None


async def test_deleting_a_user_cascades_to_their_refresh_tokens(app_schema: AppSession) -> None:
    user = await create(app_schema, UserFactory)
    await create(app_schema, RefreshTokenFactory, user_id=user.id)
    await app_schema.delete(user)
    await app_schema.flush()
    assert await rows(app_schema) == []


# ------------------------------------------------------------------------------ job


async def test_the_cleanup_job_entry_point_deletes_old_rows_commits_and_logs_the_count(
    app_schema: AppSession, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    user = await create(app_schema, UserFactory)
    now = datetime.now(UTC)
    old = await create(
        app_schema, RefreshTokenFactory, user_id=user.id, expires_at=now - timedelta(days=8)
    )
    recent = await create(
        app_schema, RefreshTokenFactory, user_id=user.id, expires_at=now - timedelta(days=6)
    )
    live = await create(
        app_schema, RefreshTokenFactory, user_id=user.id, expires_at=now + timedelta(days=1)
    )
    old_id, recent_id, live_id = old.id, recent.id, live.id
    connection = await app_schema.connection()
    factory = async_sessionmaker(
        connection,
        class_=AppSession,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )
    monkeypatch.setattr(jobs_module, "get_sessionmaker", lambda: factory)

    with caplog.at_level(logging.INFO, logger=jobs_module.logger.name):
        deleted = await cleanup_refresh_tokens_job({})

    assert deleted == 1
    remaining = await _row_ids(app_schema)
    assert remaining == {recent_id, live_id}
    assert old_id not in remaining
    assert any("deleted 1 expired refresh token" in r.getMessage() for r in caplog.records)


def test_the_cleanup_job_is_registered_under_its_canonical_name() -> None:
    assert JOB in JOBS
    assert JOBS[JOB].fn is services.cleanup_refresh_tokens_job


def test_the_cleanup_job_runs_once_a_day_on_the_utc_clock() -> None:
    schedule = SCHEDULES[JOB]

    def one(value: Any) -> int:
        if isinstance(value, int):
            return value
        assert isinstance(value, set | list | tuple)
        assert len(value) == 1
        return int(next(iter(value)))

    assert one(schedule.hour) == 4
    assert one(schedule.minute) == 15
    assert schedule.day is None
    assert schedule.weekday is None
    assert schedule.month is None
    first = schedule.next_run(datetime(2026, 11, 15, 0, 0, tzinfo=UTC))
    second = schedule.next_run(first)
    assert second - first == timedelta(days=1)


def test_the_worker_builds_a_cron_job_for_the_cleanup_outside_tests() -> None:
    settings = get_settings().model_copy(update={"app_env": "prod", "scheduler_enabled": True})
    assert JOB in [job.name for job in build_cron_jobs(settings)]
