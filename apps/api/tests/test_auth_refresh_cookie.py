"""Refresh token transport: cookie for web, body for mobile (S-2.1.5 AC3)."""

import httpx
import pytest

from app.core.config import get_settings
from app.core.db import AppSession
from tests.refresh_support import (
    COOKIE_NAME,
    COOKIE_PATH,
    LOGIN,
    MOBILE,
    PASSWORD,
    REFRESH,
    assert_cookie_cleared,
    cookie_attributes,
    cookie_header,
    cookie_value,
    decode,
    login_request,
    make_user,
    post_refresh,
    row_for,
    set_cookies,
    sign_in,
)

pytestmark = pytest.mark.usefixtures("app_schema")


def _assert_cookie_attributes(raw: str, max_age: int) -> None:
    attributes = cookie_attributes(raw)
    assert "httponly" in attributes
    assert "secure" in attributes
    assert attributes["samesite"].lower() == "strict"
    assert attributes["path"] == COOKIE_PATH
    assert attributes["max-age"] == str(max_age)
    assert "domain" not in attributes


# ------------------------------------------------------------------------------ web


async def test_web_login_sets_one_hardened_refresh_cookie(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    user = await make_user(app_schema)
    response = await login_request(client, "web")
    assert response.status_code == 200
    (raw,) = set_cookies(response)
    _assert_cookie_attributes(raw, 30 * 24 * 3600)
    claims = decode(cookie_value(response))
    assert (claims["type"], claims["sub"]) == ("refresh", str(user.id))


async def test_web_login_body_never_contains_the_refresh_token(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_user(app_schema)
    response = await login_request(client, "web")
    token = cookie_value(response)
    body = response.json()
    assert "refresh_token" not in body
    assert set(body) == {"access_token", "token_type", "expires_in"}
    assert token not in response.text


async def test_cookie_max_age_follows_the_refresh_lifetime_setting(
    client: httpx.AsyncClient, app_schema: AppSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_user(app_schema)
    monkeypatch.setenv("REFRESH_TOKEN_TTL_DAYS", "2")
    get_settings.cache_clear()
    try:
        response = await login_request(client, "web")
    finally:
        get_settings.cache_clear()
    _assert_cookie_attributes(set_cookies(response)[0], 2 * 24 * 3600)


async def test_web_refresh_sets_a_new_hardened_cookie_and_keeps_the_token_out_of_the_body(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_user(app_schema)
    _, old = await sign_in(client, "web")
    response = await post_refresh(client, "web", old)
    assert response.status_code == 200
    (raw,) = set_cookies(response)
    _assert_cookie_attributes(raw, 30 * 24 * 3600)
    new = cookie_value(response)
    assert new != old
    assert decode(new)["type"] == "refresh"
    assert new not in response.text
    assert response.json().get("refresh_token") is None
    assert (await row_for(app_schema, new)).revoked_at is None


async def test_web_refresh_ignores_a_token_in_the_body(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_user(app_schema)
    _, token = await sign_in(client, "web")
    response = await client.post(REFRESH, json={"refresh_token": token})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"
    assert (await row_for(app_schema, token)).revoked_at is None
    assert (await post_refresh(client, "web", token)).status_code == 200


async def test_a_web_401_clears_the_cookie(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_user(app_schema)
    _, token = await sign_in(client, "web")
    await post_refresh(client, "web", token)
    reused = await post_refresh(client, "web", token)
    assert reused.status_code == 401
    assert_cookie_cleared(reused)


@pytest.mark.parametrize("token", ["garbage", None])
async def test_a_web_401_for_a_bad_or_missing_token_clears_the_cookie(
    client: httpx.AsyncClient, token: str | None
) -> None:
    headers = cookie_header(token) if token else {}
    response = await client.post(REFRESH, headers=headers)
    assert response.status_code == 401
    assert_cookie_cleared(response)


# ------------------------------------------------------------------------------ mobile


async def test_mobile_login_returns_the_refresh_token_in_the_body_and_sets_no_cookie(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    user = await make_user(app_schema)
    response = await login_request(client, "mobile")
    assert response.status_code == 200
    token = response.json()["refresh_token"]
    claims = decode(token)
    assert (claims["type"], claims["sub"]) == ("refresh", str(user.id))
    assert response.headers.get_list("set-cookie") == []


async def test_mobile_refresh_returns_the_new_token_in_the_body_and_sets_no_cookie(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_user(app_schema)
    _, old = await sign_in(client, "mobile")
    response = await post_refresh(client, "mobile", old)
    assert response.status_code == 200
    new = response.json()["refresh_token"]
    assert new != old
    assert decode(new)["type"] == "refresh"
    assert response.headers.get_list("set-cookie") == []


async def test_mobile_refresh_ignores_the_cookie(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_user(app_schema)
    _, token = await sign_in(client, "mobile")
    response = await client.post(REFRESH, headers={**MOBILE, **cookie_header(token)})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"
    assert (await row_for(app_schema, token)).revoked_at is None


async def test_a_mobile_401_sets_no_cookie(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_user(app_schema)
    _, token = await sign_in(client, "mobile")
    await post_refresh(client, "mobile", token)
    reused = await post_refresh(client, "mobile", token)
    assert reused.status_code == 401
    assert reused.headers.get_list("set-cookie") == []


# ------------------------------------------------------------------------------ mode selection


async def test_x_client_mobile_with_an_origin_header_is_treated_as_web(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_user(app_schema)
    response = await client.post(
        LOGIN,
        json={"username": "ada", "password": PASSWORD},
        headers={**MOBILE, "Origin": "https://evil.example"},
    )
    assert response.status_code == 200
    assert "refresh_token" not in response.json()
    assert len(set_cookies(response)) == 1


async def test_an_origin_header_without_x_client_is_web(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_user(app_schema)
    response = await client.post(
        LOGIN,
        json={"username": "ada", "password": PASSWORD},
        headers={"Origin": "http://localhost:5173"},
    )
    assert "refresh_token" not in response.json()
    assert len(set_cookies(response)) == 1


@pytest.mark.parametrize("value", ["MOBILE", "Mobile", " mobile "])
async def test_x_client_value_is_case_insensitive(
    client: httpx.AsyncClient, app_schema: AppSession, value: str
) -> None:
    await make_user(app_schema)
    response = await client.post(
        LOGIN, json={"username": "ada", "password": PASSWORD}, headers={"X-Client": value}
    )
    assert isinstance(response.json()["refresh_token"], str)
    assert response.headers.get_list("set-cookie") == []


@pytest.mark.parametrize("value", ["web", "ios", "mobile-app", ""])
async def test_any_other_x_client_value_is_web(
    client: httpx.AsyncClient, app_schema: AppSession, value: str
) -> None:
    await make_user(app_schema)
    response = await client.post(
        LOGIN, json={"username": "ada", "password": PASSWORD}, headers={"X-Client": value}
    )
    assert "refresh_token" not in response.json()
    assert len(set_cookies(response)) == 1


async def test_mobile_header_with_an_origin_cannot_use_a_body_token_on_refresh(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_user(app_schema)
    _, token = await sign_in(client, "mobile")
    response = await client.post(
        REFRESH,
        json={"refresh_token": token},
        headers={**MOBILE, "Origin": "https://evil.example"},
    )
    assert response.status_code == 401
    assert (await row_for(app_schema, token)).revoked_at is None


def test_cookie_constants_in_core_auth_match_the_contract() -> None:
    from app.core import auth  # noqa: PLC0415

    assert auth.REFRESH_COOKIE_NAME == COOKIE_NAME == "mtgstats_refresh"
    assert auth.REFRESH_COOKIE_PATH == COOKIE_PATH == "/api/v1/auth"
