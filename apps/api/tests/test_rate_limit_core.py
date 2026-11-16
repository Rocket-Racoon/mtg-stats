"""The Redis fixed-window limiter in ``core``: fail open, TTL on first increment (S-2.1.10)."""

import asyncio
import logging
import time

import httpx
import pytest
import redis.asyncio.client as redis_client
from fastapi import FastAPI
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.core.db import AppSession
from app.core.redis import close_redis
from tests.rate_limit_support import (
    PASSWORD,
    keys_under,
    login,
    make_member,
    use_settings,
)
from tests.register_support import (
    PREVIEW,
    make_inviter,
    make_token_invite,
    new_token,
    register,
)

pytestmark = pytest.mark.usefixtures("app_schema")


@pytest.fixture(autouse=True)
async def _committed_schema(app_schema: AppSession) -> None:
    """Release the savepoint so the app's error-path rollback keeps the schema (and any setup)."""
    await app_schema.commit()


LOGGER = "app.core.rate_limit"
WRONG = "wrong password!"


@pytest.fixture(
    params=["connection_refused", "redis_timeout", "redis_connection_error", "redis_hangs"]
)
async def redis_down(
    request: pytest.FixtureRequest,
    api_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> str:
    """Make every limiter call fail in one of four ways; capture the limiter's logger.

    Every limit is 1 and the call timeout is short, so two or three requests are already past
    each limit and nothing in these tests waits long.
    """
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    mode: str = request.param
    use_settings(
        api_app,
        monkeypatch,
        SIGNIN_MAX_FAILURES_PER_USERNAME="1",
        SIGNIN_MAX_FAILURES_PER_IP="1",
        PUBLIC_MAX_REQUESTS_PER_IP="1",
        REDIS_CALL_TIMEOUT_SECONDS="0.1" if mode == "redis_hangs" else "0.5",
        **({"REDIS_URL": "redis://127.0.0.1:1/15"} if mode == "connection_refused" else {}),
    )
    await close_redis()
    if mode != "connection_refused":
        error: Exception = (
            RedisTimeoutError("timed out")
            if mode == "redis_timeout"
            else RedisConnectionError("gone")
        )

        async def boom(*args: object, **kwargs: object) -> None:
            if mode == "redis_hangs":
                await asyncio.sleep(30)
            raise error

        monkeypatch.setattr(redis_client.Pipeline, "execute", boom)
        monkeypatch.setattr(redis_client.Redis, "execute_command", boom)
    return mode


def _warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == LOGGER and r.levelno == logging.WARNING]


async def test_login_fails_open_when_redis_is_down(
    redis_down: str,
    client: httpx.AsyncClient,
    app_schema: AppSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await make_member(app_schema, "failopen")
    for _ in range(3):  # past every (lowered) sign-in limit
        assert (await login(client, "failopen", WRONG)).status_code == 401

    response = await login(client, "failopen", PASSWORD)

    assert response.status_code == 200
    warnings = _warnings(caplog)
    assert warnings
    assert all("failopen" not in r.getMessage() for r in caplog.records if r.name == LOGGER)


async def test_preview_fails_open_and_logs_one_warning_per_request(
    redis_down: str,
    client: httpx.AsyncClient,
    app_schema: AppSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    inviter = await make_inviter(app_schema)
    token = await make_token_invite(app_schema, inviter)

    response = await client.post(PREVIEW, json={"token": token})

    assert response.status_code == 200
    assert len(_warnings(caplog)) == 1


async def test_preview_is_never_limited_while_redis_is_down(
    redis_down: str, client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    for _ in range(3):  # past the lowered public limit
        assert (await client.post(PREVIEW, json={"token": new_token()})).status_code not in (
            429,
            500,
        )


async def test_registration_fails_open_when_redis_is_down(
    redis_down: str,
    client: httpx.AsyncClient,
    app_schema: AppSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    inviter = await make_inviter(app_schema)
    token = await make_token_invite(app_schema, inviter)

    response = await register(client, token, username="failopen-reg", mobile=True)

    assert response.status_code == 201
    assert _warnings(caplog)


async def test_a_working_redis_logs_no_limiter_warning(
    client: httpx.AsyncClient,
    app_schema: AppSession,
    caplog: pytest.LogCaptureFixture,
    rate_limit_prefix: str,
) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    await make_member(app_schema)

    assert (await login(client, "ada", WRONG)).status_code == 401
    assert (await client.post(PREVIEW, json={"token": new_token()})).status_code != 429

    assert _warnings(caplog) == []
    assert len(await keys_under(rate_limit_prefix)) == 3  # user, ip, public


async def test_a_hanging_redis_call_times_out_and_the_request_goes_through(
    api_app: FastAPI,
    client: httpx.AsyncClient,
    app_schema: AppSession,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    use_settings(api_app, monkeypatch, REDIS_CALL_TIMEOUT_SECONDS="0.1")
    await close_redis()

    async def hang(*args: object, **kwargs: object) -> None:
        await asyncio.sleep(30)

    monkeypatch.setattr(redis_client.Pipeline, "execute", hang)
    monkeypatch.setattr(redis_client.Redis, "execute_command", hang)
    token = await make_token_invite(app_schema, await make_inviter(app_schema))

    started = time.monotonic()
    response = await client.post(PREVIEW, json={"token": token})

    assert response.status_code == 200
    assert time.monotonic() - started < 5
    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert "TimeoutError" in warnings[0].getMessage()
