"""Shared per-IP limit on registration and the invite preview (S-2.1.10 AC4)."""

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.core.db import AppSession
from tests.rate_limit_support import (
    client_from,
    keys_under,
    spy_on_hashing,
    ttl_of,
    use_settings,
    value_of,
)
from tests.register_support import (
    PREVIEW,
    assert_envelope,
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


PUBLIC_KEY = ":public:ip:"  # key-name fragment, not a secret  # secrets-check: allow


async def _preview(client: httpx.AsyncClient) -> httpx.Response:
    return await client.post(PREVIEW, json={"token": new_token()})


async def _register(client: httpx.AsyncClient, index: int) -> httpx.Response:
    return await register(client, new_token(), username=f"user{index}")


async def _mixed_calls(client: httpx.AsyncClient, count: int) -> list[int]:
    """Alternate registration and preview calls; return every status code."""
    statuses = []
    for index in range(count):
        response = await (_register(client, index) if index % 2 == 0 else _preview(client))
        statuses.append(response.status_code)
    return statuses


def _assert_limited(response: httpx.Response) -> None:
    assert_envelope(response, 429, "rate_limited")
    retry_after = response.headers["Retry-After"]
    assert retry_after.isdigit()
    assert 1 <= int(retry_after) <= 900


async def test_thirty_mixed_calls_pass_and_the_thirty_first_preview_gets_429(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    statuses = await _mixed_calls(client, 30)

    assert 429 not in statuses
    _assert_limited(await _preview(client))


async def test_thirty_mixed_calls_pass_and_the_thirty_first_registration_gets_429(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    statuses = await _mixed_calls(client, 30)

    assert 429 not in statuses
    _assert_limited(await _register(client, 99))


async def test_registration_and_preview_share_one_bucket(
    client: httpx.AsyncClient, app_schema: AppSession, rate_limit_prefix: str
) -> None:
    for _ in range(15):
        await _preview(client)
    for index in range(15):
        await _register(client, index)

    keys = await keys_under(rate_limit_prefix)
    assert len(keys) == 1
    assert PUBLIC_KEY in keys[0]
    assert await value_of(keys[0]) == 30


async def test_every_request_counts_whatever_its_outcome(
    client: httpx.AsyncClient, app_schema: AppSession, rate_limit_prefix: str
) -> None:
    inviter = await make_inviter(app_schema)
    token = await make_token_invite(app_schema, inviter)
    assert (await client.post(PREVIEW, json={"token": token})).status_code == 200  # success
    assert (await client.post(PREVIEW, json={"token": new_token()})).status_code >= 400
    assert (await _register(client, 1)).status_code >= 400  # a failing registration

    (key,) = await keys_under(rate_limit_prefix)
    assert await value_of(key) == 3


async def test_another_ip_is_not_limited_by_the_first_ips_calls(
    client: httpx.AsyncClient, api_app: FastAPI, app_schema: AppSession
) -> None:
    await _mixed_calls(client, 30)
    _assert_limited(await _preview(client))

    async with client_from(api_app, "1.2.3.4") as other:
        assert (await _preview(other)).status_code != 429


async def test_a_throttled_registration_does_no_password_hashing(
    client: httpx.AsyncClient, app_schema: AppSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    for _ in range(30):
        await _preview(client)
    calls = spy_on_hashing(monkeypatch)

    response = await _register(client, 1)

    _assert_limited(response)
    assert calls == []


async def test_a_throttled_registration_consumes_nothing(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    from tests.register_support import count_users, use_count_of  # noqa: PLC0415

    inviter = await make_inviter(app_schema)
    token = await make_token_invite(app_schema, inviter)
    users_before = await count_users(app_schema)
    for _ in range(30):
        await _preview(client)

    _assert_limited(await register(client, token, username="blocked"))

    assert await count_users(app_schema) == users_before
    assert await use_count_of(app_schema, token) == 0


async def test_the_public_counter_key_has_a_ttl_within_the_window(
    client: httpx.AsyncClient, app_schema: AppSession, rate_limit_prefix: str
) -> None:
    await _preview(client)

    (key,) = await keys_under(rate_limit_prefix)
    assert key.startswith(f"{rate_limit_prefix}:public:ip:")
    assert 1 <= await ttl_of(key) <= 900


async def test_the_limit_and_window_come_from_the_settings(
    client: httpx.AsyncClient,
    api_app: FastAPI,
    app_schema: AppSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_settings(
        api_app, monkeypatch, PUBLIC_MAX_REQUESTS_PER_IP="3", RATE_LIMIT_WINDOW_SECONDS="60"
    )

    for _ in range(3):
        assert (await _preview(client)).status_code != 429
    response = await _preview(client)

    assert_envelope(response, 429, "rate_limited")
    assert 1 <= int(response.headers["Retry-After"]) <= 60


# ---------------------------------------------------------------------------- settings


def test_settings_defaults_match_the_story(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "SIGNIN_MAX_FAILURES_PER_USERNAME",
        "SIGNIN_MAX_FAILURES_PER_IP",
        "PUBLIC_MAX_REQUESTS_PER_IP",
        "RATE_LIMIT_WINDOW_SECONDS",
        "RATE_LIMIT_KEY_PREFIX",
    ):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.signin_max_failures_per_username == 5
    assert settings.signin_max_failures_per_ip == 20
    assert settings.public_max_requests_per_ip == 30
    assert settings.rate_limit_window_seconds == 900
    assert settings.rate_limit_key_prefix == "mtgstats:rl"


@pytest.mark.parametrize(
    "name",
    [
        "SIGNIN_MAX_FAILURES_PER_USERNAME",
        "SIGNIN_MAX_FAILURES_PER_IP",
        "PUBLIC_MAX_REQUESTS_PER_IP",
        "RATE_LIMIT_WINDOW_SECONDS",
    ],
)
@pytest.mark.parametrize("value", ["0", "-1"])
def test_a_limit_or_window_below_one_is_rejected(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError, match=name):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_an_empty_key_prefix_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_KEY_PREFIX", "")

    with pytest.raises(ValidationError, match="RATE_LIMIT_KEY_PREFIX"):
        Settings(_env_file=None)  # type: ignore[call-arg]
