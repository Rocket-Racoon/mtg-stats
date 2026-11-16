"""Sign-in failure throttling (S-2.1.10 AC1, AC2, AC3 and the sign-in decisions)."""

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.core.db import AppSession, get_session
from app.identity import repository
from app.identity.services import auth as auth_module
from tests.rate_limit_support import (
    client_from,
    keys_containing,
    keys_under,
    login,
    make_member,
    peek_redis,
    spy_on_hashing,
    ttl_of,
    use_settings,
    value_of,
)
from tests.register_support import assert_envelope

pytestmark = pytest.mark.usefixtures("app_schema")


@pytest.fixture(autouse=True)
async def _committed_schema(app_schema: AppSession) -> None:
    """Release the savepoint so the app's error-path rollback keeps the schema (and any setup)."""
    await app_schema.commit()


USER_KEY = ":signin:user:"  # key-name fragment, not a secret  # secrets-check: allow
IP_KEY = ":signin:ip:"  # key-name fragment, not a secret  # secrets-check: allow
WRONG = "wrong password!"


def _assert_throttled(response: httpx.Response, window: int = 900) -> int:
    assert_envelope(response, 429, "rate_limited")
    retry_after = response.headers["Retry-After"]
    assert retry_after.isdigit()
    assert 1 <= int(retry_after) <= window
    return int(retry_after)


async def _fail(client: httpx.AsyncClient, times: int, username: str = "ada") -> None:
    for _ in range(times):
        assert (await login(client, username, WRONG)).status_code == 401


# ------------------------------------------------------------------------------ AC1


async def test_sixth_attempt_after_five_failures_gets_429_even_with_the_correct_password(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_member(app_schema)
    await _fail(client, 5)

    response = await login(client)  # the correct password

    _assert_throttled(response)


async def test_fifth_failure_is_still_a_401_not_a_429(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_member(app_schema)
    await _fail(client, 4)

    assert_envelope(await login(client, "ada", WRONG), 401, "invalid_credentials")


async def test_four_failures_leave_the_correct_password_working(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_member(app_schema)
    await _fail(client, 4)

    assert (await login(client)).status_code == 200


async def test_throttled_attempt_runs_no_password_hashing(
    client: httpx.AsyncClient, app_schema: AppSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_member(app_schema)
    await _fail(client, 5)
    calls = spy_on_hashing(monkeypatch)

    response = await login(client)

    _assert_throttled(response)
    assert calls == []


async def test_throttle_applies_in_web_cookie_mode_too(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_member(app_schema)
    for _ in range(5):
        assert (await login(client, "ada", WRONG, mobile=False)).status_code == 401

    response = await login(client, mobile=False)

    _assert_throttled(response)
    assert "set-cookie" not in response.headers


async def test_username_counter_is_case_insensitive(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_member(app_schema, "ada")
    await _fail(client, 3, "Alice")
    await _fail(client, 2, "alice")

    _assert_throttled(await login(client, "ALICE"))


async def test_a_real_account_is_throttled_across_username_casing(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_member(app_schema, "ada")
    await _fail(client, 5, "ADA")

    _assert_throttled(await login(client, "ada"))


async def test_an_unknown_username_is_throttled_the_same_way(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await _fail(client, 5, "nobody-here")

    _assert_throttled(await login(client, "nobody-here"))


async def test_an_inactive_accounts_failures_count(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_member(app_schema, "ghost", active=False)
    await _fail(client, 5, "ghost")

    _assert_throttled(await login(client, "ghost"))


async def test_another_username_is_not_throttled_by_one_usernames_failures(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_member(app_schema, "ada")
    await make_member(app_schema, "bob")
    await _fail(client, 5, "ada")

    assert (await login(client, "bob")).status_code == 200


async def test_sign_in_works_again_after_the_window_ends(
    client: httpx.AsyncClient, app_schema: AppSession, rate_limit_prefix: str
) -> None:
    await make_member(app_schema)
    await _fail(client, 5)
    _assert_throttled(await login(client))
    # The window ends: expire the counters directly (a real 15-minute wait is not an option).
    async with peek_redis() as redis:
        for key in await keys_under(rate_limit_prefix):
            await redis.pexpire(key, 200)

    await asyncio.sleep(0.4)

    assert (await login(client)).status_code == 200


async def test_retry_after_is_the_remaining_ttl_of_the_blocking_counter(
    client: httpx.AsyncClient, app_schema: AppSession, rate_limit_prefix: str
) -> None:
    await make_member(app_schema)
    await _fail(client, 5)
    # Shorten the username counter to 100 s; the IP counter (5 < 20) is not blocking.
    (user_key,) = await keys_containing(rate_limit_prefix, USER_KEY)
    async with peek_redis() as redis:
        await redis.expire(user_key, 100)

    retry_after = _assert_throttled(await login(client), window=100)

    assert 90 <= retry_after <= 100


async def test_retry_after_falls_back_to_the_window_when_the_counter_has_no_ttl(
    client: httpx.AsyncClient, app_schema: AppSession, rate_limit_prefix: str
) -> None:
    await make_member(app_schema)
    await _fail(client, 5)
    (user_key,) = await keys_containing(rate_limit_prefix, USER_KEY)
    async with peek_redis() as redis:
        await redis.persist(user_key)

    assert _assert_throttled(await login(client)) == 900
    assert (
        1 <= await ttl_of(user_key) <= 900
    )  # the blocked key got a window, it cannot stay forever


# ------------------------------------------------------------------------------ AC2


async def test_twenty_failures_from_one_ip_across_accounts_block_the_next_attempt(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_member(app_schema, "ada")
    for index in range(20):
        await _fail(client, 1, f"user{index}")

    _assert_throttled(await login(client, "ada"))  # a correct password, a fresh username
    _assert_throttled(await login(client, "brand-new-name", WRONG))


async def test_nineteen_failures_from_one_ip_do_not_block(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_member(app_schema, "ada")
    for index in range(19):
        await _fail(client, 1, f"user{index}")

    assert (await login(client, "ada")).status_code == 200


async def test_another_ip_is_not_throttled_by_the_first_ips_failures(
    client: httpx.AsyncClient, api_app: FastAPI, app_schema: AppSession
) -> None:
    await make_member(app_schema, "ada")
    for index in range(20):
        await _fail(client, 1, f"user{index}")
    _assert_throttled(await login(client, "ada"))

    async with client_from(api_app, "1.2.3.4") as other:
        assert (await login(other, "ada")).status_code == 200


async def test_the_ip_counter_uses_the_asgi_client_address_not_a_forwarded_header(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    for index in range(20):
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": f"user{index}", "password": WRONG},
            headers={"X-Forwarded-For": f"9.9.9.{index}", "X-Client": "mobile"},
        )
        assert response.status_code == 401

    _assert_throttled(await login(client, "someone", WRONG))


# ------------------------------------------------------------------------------ AC3


async def test_success_resets_the_username_counter(
    client: httpx.AsyncClient, app_schema: AppSession, rate_limit_prefix: str
) -> None:
    await make_member(app_schema)
    await _fail(client, 4)

    assert (await login(client)).status_code == 200

    assert await keys_containing(rate_limit_prefix, USER_KEY) == []
    await _fail(client, 5)  # the full budget is back
    _assert_throttled(await login(client))


async def test_success_does_not_reset_the_ip_counter(
    client: httpx.AsyncClient, app_schema: AppSession, rate_limit_prefix: str
) -> None:
    await make_member(app_schema)
    await _fail(client, 4)
    (ip_key,) = await keys_containing(rate_limit_prefix, IP_KEY)
    assert await value_of(ip_key) == 4

    assert (await login(client)).status_code == 200

    assert await value_of(ip_key) == 4
    await _fail(client, 5)
    assert await value_of(ip_key) == 9


# ------------------------------------------------------------------------------ decisions


async def test_a_validation_error_does_not_count_as_a_failure(
    client: httpx.AsyncClient, app_schema: AppSession, rate_limit_prefix: str
) -> None:
    await make_member(app_schema)
    for _ in range(6):
        response = await client.post("/api/v1/auth/login", json={"username": "ada"})
        assert response.status_code == 422

    assert await keys_under(rate_limit_prefix) == []
    assert (await login(client)).status_code == 200


async def test_counter_keys_have_a_ttl_within_the_window_and_hide_the_raw_username(
    client: httpx.AsyncClient, app_schema: AppSession, rate_limit_prefix: str
) -> None:
    await _fail(client, 1, "Secret.Name")

    keys = await keys_under(rate_limit_prefix)
    assert len(keys) == 2
    assert any(USER_KEY in key for key in keys)
    assert any(IP_KEY in key for key in keys)
    for key in keys:
        assert key.startswith(f"{rate_limit_prefix}:signin:")
        assert "secret" not in key.lower()
        assert 1 <= await ttl_of(key) <= 900


async def test_the_ttl_is_set_on_the_first_failure_and_not_extended_by_later_ones(
    client: httpx.AsyncClient, app_schema: AppSession, rate_limit_prefix: str
) -> None:
    await _fail(client, 1)
    (user_key,) = await keys_containing(rate_limit_prefix, USER_KEY)
    async with peek_redis() as redis:
        await redis.expire(user_key, 100)

    await _fail(client, 1)

    assert 1 <= await ttl_of(user_key) <= 100  # fixed window: the second failure kept the TTL


async def test_the_limits_come_from_the_settings(
    client: httpx.AsyncClient,
    api_app: FastAPI,
    app_schema: AppSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_settings(
        api_app,
        monkeypatch,
        SIGNIN_MAX_FAILURES_PER_USERNAME="2",
        SIGNIN_MAX_FAILURES_PER_IP="3",
    )
    await make_member(app_schema, "ada")
    await _fail(client, 2, "ada")
    _assert_throttled(await login(client, "ada"))  # 2 failures for the username

    await _fail(client, 1, "other")  # the IP is now at 3
    _assert_throttled(await login(client, "third-name", WRONG))


# ------------------------------------------------------------------------------ amended counting


async def test_a_throttled_attempt_does_not_increment_either_counter(
    client: httpx.AsyncClient, app_schema: AppSession, rate_limit_prefix: str
) -> None:
    await make_member(app_schema)
    await _fail(client, 5)
    (user_key,) = await keys_containing(rate_limit_prefix, USER_KEY)
    (ip_key,) = await keys_containing(rate_limit_prefix, IP_KEY)

    for _ in range(3):
        _assert_throttled(await login(client))

    assert await value_of(user_key) == 5
    assert await value_of(ip_key) == 5


async def test_an_ip_throttled_attempt_does_not_increment_either_counter(
    client: httpx.AsyncClient, app_schema: AppSession, rate_limit_prefix: str
) -> None:
    for index in range(20):
        await _fail(client, 1, f"user{index}")
    (ip_key,) = await keys_containing(rate_limit_prefix, IP_KEY)

    _assert_throttled(await login(client, "fresh-name", WRONG))

    assert await value_of(ip_key) == 20
    assert await keys_containing(rate_limit_prefix, USER_KEY) != []
    for key in await keys_containing(rate_limit_prefix, USER_KEY):
        assert await value_of(key) == 1  # "fresh-name" never got a counter


async def test_an_unexpected_error_does_not_leave_a_counted_attempt(
    client: httpx.AsyncClient,
    app_schema: AppSession,
    rate_limit_prefix: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("database exploded")

    monkeypatch.setattr(repository, "get_user_by_username", boom)

    assert (await login(client, "ada", WRONG)).status_code == 500

    for key in await keys_under(rate_limit_prefix):
        assert await value_of(key) == 0


# ------------------------------------------------------------------------------ concurrency


@pytest.fixture
def stub_lookup(api_app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Make every sign-in an unknown-user failure without touching the shared DB session.

    The user lookup sleeps a little, which widens the window between a limiter check and the
    count, so a peek-then-hit design lets every concurrent attempt through. Returns a list that
    gets one entry per ``verify_password`` call (argon2 work).
    """
    verifies: list[int] = []

    async def no_user(*args: object, **kwargs: object) -> None:
        await asyncio.sleep(0.05)

    def counting_verify(*args: object, **kwargs: object) -> bool:
        verifies.append(1)
        return False

    async def no_session() -> AsyncIterator[AsyncMock]:
        yield AsyncMock()  # unused by the stubbed lookup; one real session cannot run in parallel

    monkeypatch.setattr(repository, "get_user_by_username", no_user)
    monkeypatch.setattr(auth_module, "verify_password", counting_verify)
    api_app.dependency_overrides[get_session] = no_session
    return verifies


async def test_concurrent_wrong_passwords_for_one_username_let_at_most_five_reach_argon2(
    client: httpx.AsyncClient, stub_lookup: list[int], rate_limit_prefix: str
) -> None:
    responses = await asyncio.gather(*(login(client, "ada", WRONG) for _ in range(10)))

    statuses = sorted(r.status_code for r in responses)
    assert statuses == [401] * 5 + [429] * 5
    assert len(stub_lookup) == 5
    (user_key,) = await keys_containing(rate_limit_prefix, USER_KEY)
    assert await value_of(user_key) == 5


async def test_concurrent_attempts_from_one_ip_let_at_most_twenty_reach_argon2(
    client: httpx.AsyncClient, stub_lookup: list[int], rate_limit_prefix: str
) -> None:
    responses = await asyncio.gather(*(login(client, f"user{i}", WRONG) for i in range(30)))

    statuses = sorted(r.status_code for r in responses)
    assert statuses == [401] * 20 + [429] * 10
    assert len(stub_lookup) == 20
    (ip_key,) = await keys_containing(rate_limit_prefix, IP_KEY)
    assert await value_of(ip_key) == 20
