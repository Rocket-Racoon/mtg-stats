"""Refresh token rotation and reuse detection (S-2.1.5 AC1, AC2, added scope: inactive user)."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import update

from app.core.auth import CurrentUserDep, InvalidTokenError
from app.core.db import AppSession
from app.core.errors import DomainError
from app.identity import services
from app.identity.models import RefreshToken
from app.identity.services import RefreshTokenReuseError, refresh_session
from tests.auth_support import ACCESS, REFRESH, claims_for, encode, issue
from tests.factories import create
from tests.refresh_support import (
    MOBILE,
    PASSWORD,
    Mode,
    RefreshTokenFactory,
    bearer,
    deactivate,
    decode,
    make_user,
    post_logout,
    post_refresh,
    refresh_token_of,
    row_for,
    rows,
    sign_in,
)
from tests.refresh_support import (
    REFRESH as REFRESH_URL,
)

pytestmark = pytest.mark.usefixtures("app_schema")
MODES: list[Mode] = ["web", "mobile"]


@pytest.fixture
async def protected(api_app: FastAPI, client: httpx.AsyncClient) -> httpx.AsyncClient:
    async def route(user: CurrentUserDep) -> dict[str, str]:
        return {"id": str(user.id)}

    api_app.add_api_route("/_test/protected", route, methods=["GET"])
    return client


# ------------------------------------------------------------------------------ AC1: rotation


@pytest.mark.parametrize("mode", MODES)
async def test_refresh_returns_a_new_access_token_and_a_new_refresh_token(
    client: httpx.AsyncClient, app_schema: AppSession, mode: Mode
) -> None:
    user = await make_user(app_schema)
    _, old = await sign_in(client, mode)

    response = await post_refresh(client, mode, old)

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 900
    access = decode(body["access_token"])
    assert (access["type"], access["sub"]) == ("access", str(user.id))
    new = refresh_token_of(response, mode)
    assert new != old
    refresh = decode(new)
    assert (refresh["type"], refresh["sub"]) == ("refresh", str(user.id))
    assert refresh["exp"] - refresh["iat"] == 30 * 24 * 3600


@pytest.mark.parametrize("mode", MODES)
async def test_refresh_revokes_the_old_token_and_links_it_to_the_new_row_in_the_same_family(
    client: httpx.AsyncClient, app_schema: AppSession, mode: Mode
) -> None:
    await make_user(app_schema)
    _, old = await sign_in(client, mode)
    response = await post_refresh(client, mode, old)
    new = refresh_token_of(response, mode)

    old_row, new_row = await row_for(app_schema, old), await row_for(app_schema, new)

    assert old_row.revoked_at is not None
    assert old_row.replaced_by == new_row.id
    assert new_row.revoked_at is None
    assert new_row.family_id == old_row.family_id
    assert new_row.user_id == old_row.user_id
    assert new_row.replaced_by is None


@pytest.mark.parametrize("mode", MODES)
async def test_the_rotated_refresh_token_can_be_rotated_again_within_one_family(
    client: httpx.AsyncClient, app_schema: AppSession, mode: Mode
) -> None:
    await make_user(app_schema)
    _, first = await sign_in(client, mode)
    second = refresh_token_of(await post_refresh(client, mode, first), mode)
    response = await post_refresh(client, mode, second)
    assert response.status_code == 200
    third = refresh_token_of(response, mode)
    assert len({first, second, third}) == 3
    all_rows = await rows(app_schema)
    assert len({r.family_id for r in all_rows}) == 1
    assert [r.revoked_at is not None for r in all_rows] == [True, True, False]


@pytest.mark.parametrize("mode", MODES)
async def test_the_new_access_token_works_on_a_protected_route(
    protected: httpx.AsyncClient, app_schema: AppSession, mode: Mode
) -> None:
    user = await make_user(app_schema)
    _, old = await sign_in(protected, mode)
    access = (await post_refresh(protected, mode, old)).json()["access_token"]
    response = await protected.get("/_test/protected", headers=bearer(access))
    assert response.status_code == 200
    assert response.json() == {"id": str(user.id)}


@pytest.mark.parametrize("mode", MODES)
async def test_the_old_refresh_token_stops_working_after_rotation(
    client: httpx.AsyncClient, app_schema: AppSession, mode: Mode
) -> None:
    await make_user(app_schema)
    _, old = await sign_in(client, mode)
    await post_refresh(client, mode, old)
    response = await post_refresh(client, mode, old)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


@pytest.mark.parametrize("mode", MODES)
async def test_a_token_that_decodes_but_is_unknown_to_the_database_is_rejected_without_writes(
    client: httpx.AsyncClient, app_schema: AppSession, mode: Mode
) -> None:
    user = await make_user(app_schema)
    _, real = await sign_in(client, mode)
    forged = issue(user.id, REFRESH)
    response = await post_refresh(client, mode, forged)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"
    assert all(r.revoked_at is None for r in await rows(app_schema))
    assert (await post_refresh(client, mode, real)).status_code == 200


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("token", ["garbage", "a.b.c"])
async def test_a_malformed_refresh_token_is_401_invalid_token(
    client: httpx.AsyncClient, app_schema: AppSession, mode: Mode, token: str
) -> None:
    await make_user(app_schema)
    response = await post_refresh(client, mode, token)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


@pytest.mark.parametrize("mode", MODES)
async def test_an_access_token_presented_as_a_refresh_token_is_401_invalid_token(
    client: httpx.AsyncClient, app_schema: AppSession, mode: Mode
) -> None:
    user = await make_user(app_schema)
    response = await post_refresh(client, mode, issue(user.id, ACCESS))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


@pytest.mark.parametrize("mode", MODES)
async def test_an_expired_refresh_token_is_401_invalid_token(
    client: httpx.AsyncClient, app_schema: AppSession, mode: Mode
) -> None:
    user = await make_user(app_schema)
    claims = claims_for(user.id, token_type="refresh", ttl=-60)
    response = await post_refresh(client, mode, encode(claims))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


@pytest.mark.parametrize("mode", MODES)
async def test_a_token_whose_stored_row_has_expired_is_401_invalid_token(
    client: httpx.AsyncClient, app_schema: AppSession, mode: Mode
) -> None:
    await make_user(app_schema)
    _, token = await sign_in(client, mode)
    await app_schema.execute(
        update(RefreshToken).values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
    )
    await app_schema.commit()
    response = await post_refresh(client, mode, token)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


async def test_a_web_request_without_the_cookie_is_401_unauthenticated(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(REFRESH_URL)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


@pytest.mark.parametrize("body", [None, {}, {"refresh_token": None}])
async def test_a_mobile_request_without_a_token_is_401_unauthenticated(
    client: httpx.AsyncClient, body: dict[str, None] | None
) -> None:
    response = await client.post(REFRESH_URL, json=body, headers=MOBILE)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


# ------------------------------------------------------------------------------ AC2: reuse


@pytest.mark.parametrize("mode", MODES)
async def test_reusing_a_rotated_token_is_401_and_revokes_the_whole_family_persistently(
    client: httpx.AsyncClient, app_schema: AppSession, mode: Mode
) -> None:
    await make_user(app_schema)
    _, first = await sign_in(client, mode)
    second = refresh_token_of(await post_refresh(client, mode, first), mode)
    third = refresh_token_of(await post_refresh(client, mode, second), mode)
    assert len(await rows(app_schema)) == 3

    response = await post_refresh(client, mode, first)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"
    assert "invalid_token" in response.headers["www-authenticate"]
    after = await rows(app_schema)
    assert len(after) == 3
    assert all(r.revoked_at is not None for r in after)
    # the newest token of the family is dead too
    assert (await post_refresh(client, mode, third)).status_code == 401


@pytest.mark.parametrize("mode", MODES)
async def test_reuse_leaves_the_users_other_families_untouched(
    client: httpx.AsyncClient, app_schema: AppSession, mode: Mode
) -> None:
    await make_user(app_schema)
    _, phone = await sign_in(client, mode)
    _, laptop = await sign_in(client, mode)
    rotated = refresh_token_of(await post_refresh(client, mode, laptop), mode)
    laptop_family = (await row_for(app_schema, laptop)).family_id
    assert (await row_for(app_schema, phone)).family_id != laptop_family

    assert (await post_refresh(client, mode, laptop)).status_code == 401

    assert (await row_for(app_schema, phone)).revoked_at is None
    assert (await row_for(app_schema, rotated)).revoked_at is not None
    assert (await post_refresh(client, mode, phone)).status_code == 200


@pytest.mark.parametrize("mode", MODES)
async def test_reuse_leaves_another_users_family_untouched(
    client: httpx.AsyncClient, app_schema: AppSession, mode: Mode
) -> None:
    await make_user(app_schema, "ada")
    await make_user(app_schema, "bob")
    _, ada = await sign_in(client, mode, "ada")
    _, bob = await sign_in(client, mode, "bob")
    await post_refresh(client, mode, ada)
    assert (await post_refresh(client, mode, ada)).status_code == 401
    assert (await row_for(app_schema, bob)).revoked_at is None
    assert (await post_refresh(client, mode, bob)).status_code == 200


@pytest.mark.parametrize("mode", MODES)
async def test_each_sign_in_starts_its_own_family(
    client: httpx.AsyncClient, app_schema: AppSession, mode: Mode
) -> None:
    await make_user(app_schema)
    await sign_in(client, mode)
    await sign_in(client, mode)
    first, second = await rows(app_schema)
    assert first.family_id != second.family_id


@pytest.mark.parametrize("mode", MODES)
async def test_presenting_a_logged_out_token_is_401_and_revokes_the_whole_family(
    client: httpx.AsyncClient, app_schema: AppSession, mode: Mode
) -> None:
    user = await make_user(app_schema)
    _, token = await sign_in(client, mode)
    family = (await row_for(app_schema, token)).family_id
    sibling = await create(
        app_schema,
        RefreshTokenFactory,
        user_id=user.id,
        family_id=family,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    sibling_id = sibling.id
    await app_schema.commit()
    assert (await post_logout(client, mode, token)).status_code == 204

    response = await post_refresh(client, mode, token)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"
    family_rows = await rows(app_schema, family_id=family)
    assert len(family_rows) == 2
    assert sibling_id in {r.id for r in family_rows}
    assert all(r.revoked_at is not None for r in family_rows)


# ------------------------------------------------------------------------------ inactive user


@pytest.mark.parametrize("mode", MODES)
async def test_an_inactive_user_refreshing_gets_401_and_the_family_is_revoked_persistently(
    client: httpx.AsyncClient, app_schema: AppSession, mode: Mode
) -> None:
    user = await make_user(app_schema)
    _, token = await sign_in(client, mode)
    _, other_session = await sign_in(client, mode)
    await deactivate(app_schema, user)
    family = (await row_for(app_schema, token)).family_id

    response = await post_refresh(client, mode, token)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"
    family_rows = await rows(app_schema, family_id=family)
    assert len(family_rows) == 1  # no new token was issued
    assert family_rows[0].revoked_at is not None
    # only that family: the user's other session is not touched by this request
    assert (await row_for(app_schema, other_session)).revoked_at is None


# ------------------------------------------------------------------------------ service level


def test_reuse_error_is_an_invalid_token_error_that_persists_its_changes() -> None:
    assert issubclass(RefreshTokenReuseError, InvalidTokenError)
    assert RefreshTokenReuseError.persist_changes is True
    assert RefreshTokenReuseError.code == "invalid_token"
    assert RefreshTokenReuseError.status_code == 401


def test_ordinary_domain_errors_do_not_persist_their_changes() -> None:
    assert DomainError.persist_changes is False
    assert InvalidTokenError.persist_changes is False


async def test_refresh_session_returns_the_user_and_both_new_tokens(
    app_schema: AppSession,
) -> None:
    user = await make_user(app_schema)
    old = await services.sign_in(app_schema, username="ada", password=PASSWORD)

    result = await refresh_session(app_schema, refresh_token=old.refresh_token.token)

    assert result.user_id == user.id
    assert decode(result.access_token.token)["type"] == "access"
    assert decode(result.refresh_token.token)["jti"] == result.refresh_token.jti
    assert result.refresh_token.token != old.refresh_token.token


async def test_refresh_session_raises_reuse_error_and_revokes_the_family_in_the_session(
    app_schema: AppSession,
) -> None:
    await make_user(app_schema)
    first = await services.sign_in(app_schema, username="ada", password=PASSWORD)
    await refresh_session(app_schema, refresh_token=first.refresh_token.token)

    with pytest.raises(RefreshTokenReuseError):
        await refresh_session(app_schema, refresh_token=first.refresh_token.token)

    assert all(r.revoked_at is not None for r in await rows(app_schema))


async def test_refresh_session_raises_invalid_token_error_for_garbage(
    app_schema: AppSession,
) -> None:
    with pytest.raises(InvalidTokenError) as caught:
        await refresh_session(app_schema, refresh_token="garbage")  # secrets-check: allow
    assert not isinstance(caught.value, RefreshTokenReuseError)
