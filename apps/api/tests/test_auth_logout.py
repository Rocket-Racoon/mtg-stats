"""Sign-out: ``POST /auth/logout`` and ``POST /auth/logout-all`` (S-2.1.5 AC4)."""

import httpx
import pytest

from app.core.db import AppSession
from tests.auth_support import ACCESS, claims_for, encode, issue
from tests.refresh_support import (
    LOGOUT,
    LOGOUT_ALL,
    MOBILE,
    Mode,
    assert_cookie_cleared,
    bearer,
    cookie_header,
    make_user,
    post_logout,
    post_refresh,
    row_for,
    rows,
    sign_in,
)

pytestmark = pytest.mark.usefixtures("app_schema")
MODES: list[Mode] = ["web", "mobile"]


# ------------------------------------------------------------------------------ logout


@pytest.mark.parametrize("mode", MODES)
async def test_logout_revokes_only_the_presented_token_and_returns_204(
    client: httpx.AsyncClient, app_schema: AppSession, mode: Mode
) -> None:
    await make_user(app_schema)
    _, phone = await sign_in(client, mode)
    _, laptop = await sign_in(client, mode)

    response = await post_logout(client, mode, phone)

    assert response.status_code == 204
    assert response.content == b""
    assert (await row_for(app_schema, phone)).revoked_at is not None
    assert (await row_for(app_schema, laptop)).revoked_at is None
    assert (await post_refresh(client, mode, laptop)).status_code == 200


@pytest.mark.parametrize("mode", MODES)
async def test_a_logged_out_token_can_no_longer_refresh(
    client: httpx.AsyncClient, app_schema: AppSession, mode: Mode
) -> None:
    await make_user(app_schema)
    _, token = await sign_in(client, mode)
    await post_logout(client, mode, token)
    response = await post_refresh(client, mode, token)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


async def test_mobile_logout_and_logout_all_send_no_set_cookie(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_user(app_schema)
    access, token = await sign_in(client, "mobile")
    logout = await post_logout(client, "mobile", token)
    logout_all = await client.post(LOGOUT_ALL, headers={**bearer(access), **MOBILE})
    assert (logout.status_code, logout_all.status_code) == (204, 204)
    assert logout.headers.get_list("set-cookie") == []
    assert logout_all.headers.get_list("set-cookie") == []


async def test_web_logout_clears_the_cookie(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_user(app_schema)
    _, token = await sign_in(client, "web")
    response = await post_logout(client, "web", token)
    assert response.status_code == 204
    assert_cookie_cleared(response)


async def test_web_logout_without_a_cookie_is_204_revokes_nothing_and_clears_the_cookie(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_user(app_schema)
    await sign_in(client, "web")
    response = await client.post(LOGOUT)
    assert response.status_code == 204
    assert_cookie_cleared(response)
    assert all(r.revoked_at is None for r in await rows(app_schema))


async def test_mobile_logout_without_a_token_is_204_and_revokes_nothing(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_user(app_schema)
    await sign_in(client, "mobile")
    for body in (None, {}, {"refresh_token": None}):
        response = await client.post(LOGOUT, json=body, headers=MOBILE)
        assert response.status_code == 204
    assert all(r.revoked_at is None for r in await rows(app_schema))


@pytest.mark.parametrize("mode", MODES)
async def test_logout_with_an_access_or_expired_token_is_204_and_revokes_nothing(
    client: httpx.AsyncClient, app_schema: AppSession, mode: Mode
) -> None:
    user = await make_user(app_schema)
    await sign_in(client, mode)
    expired = encode(claims_for(user.id, token_type="refresh", ttl=-60))
    for token in (issue(user.id, ACCESS), expired):
        assert (await post_logout(client, mode, token)).status_code == 204
    assert all(r.revoked_at is None for r in await rows(app_schema))


async def test_web_logout_ignores_a_token_in_the_body(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_user(app_schema)
    _, token = await sign_in(client, "web")
    response = await client.post(LOGOUT, json={"refresh_token": token})
    assert response.status_code == 204
    assert (await row_for(app_schema, token)).revoked_at is None


async def test_mobile_logout_ignores_the_cookie(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_user(app_schema)
    _, token = await sign_in(client, "mobile")
    response = await client.post(LOGOUT, headers={**MOBILE, **cookie_header(token)})
    assert response.status_code == 204
    assert (await row_for(app_schema, token)).revoked_at is None


async def test_logout_does_not_need_a_bearer_token(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_user(app_schema)
    _, token = await sign_in(client, "web")
    response = await client.post(LOGOUT, headers=cookie_header(token))
    assert response.status_code == 204


# ------------------------------------------------------------------------------ logout-all


@pytest.mark.parametrize("mode", MODES)
async def test_logout_all_revokes_every_refresh_token_of_the_user_only(
    client: httpx.AsyncClient, app_schema: AppSession, mode: Mode
) -> None:
    ada = await make_user(app_schema, "ada")
    await make_user(app_schema, "bob")
    access, phone = await sign_in(client, mode, "ada")
    _, laptop = await sign_in(client, mode, "ada")
    _, bob = await sign_in(client, mode, "bob")
    rotated = await post_refresh(client, mode, laptop)
    assert rotated.status_code == 200

    extra = cookie_header(phone) if mode == "web" else MOBILE
    headers = {**bearer(access), **extra}
    response = await client.post(LOGOUT_ALL, headers=headers)

    assert response.status_code == 204
    assert response.content == b""
    ada_rows = await rows(app_schema, user_id=ada.id)
    assert len(ada_rows) == 3
    assert all(r.revoked_at is not None for r in ada_rows)
    assert (await row_for(app_schema, bob)).revoked_at is None
    for token in (phone, laptop):
        assert (await post_refresh(client, mode, token)).status_code == 401
    assert (await post_refresh(client, mode, bob)).status_code == 200


async def test_web_logout_all_clears_the_cookie(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_user(app_schema)
    access, _ = await sign_in(client, "web")
    response = await client.post(LOGOUT_ALL, headers=bearer(access))
    assert response.status_code == 204
    assert_cookie_cleared(response)


async def test_logout_all_without_a_bearer_token_is_401_unauthenticated_and_revokes_nothing(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_user(app_schema)
    _, token = await sign_in(client, "web")
    response = await client.post(LOGOUT_ALL, headers=cookie_header(token))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"
    assert all(r.revoked_at is None for r in await rows(app_schema))


async def test_logout_all_rejects_a_refresh_token_used_as_the_bearer(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_user(app_schema)
    _, refresh = await sign_in(client, "mobile")
    response = await client.post(LOGOUT_ALL, headers=bearer(refresh))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"
    assert all(r.revoked_at is None for r in await rows(app_schema))
