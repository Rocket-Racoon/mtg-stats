"""``POST /auth/register`` (S-2.1.3 AC2, AC3, AC5, AC6 and the token-delivery decision)."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text

from app.core.auth import CurrentUserDep
from app.core.db import AppSession
from app.identity.services import create_user_with_player
from tests.refresh_support import (
    COOKIE_PATH,
    LOGIN,
    MOBILE,
    PASSWORD,
    bearer,
    cookie_attributes,
    cookie_value,
    decode,
    post_refresh,
    rows,
    set_cookies,
)
from tests.register_support import (
    REGISTER,
    assert_envelope,
    count_players,
    count_users,
    make_inviter,
    make_token_invite,
    new_token,
    register,
    scalar,
    use_count_of,
)

pytestmark = pytest.mark.usefixtures("app_schema")
PROTECTED = "/_test/whoami"


@pytest.fixture
def protected(api_app: FastAPI) -> str:
    """Add a bearer-protected route to the test app; yield its path."""

    async def whoami(user: CurrentUserDep) -> dict[str, str]:
        return {"id": str(user.id)}

    api_app.add_api_route(PROTECTED, whoami, methods=["GET"])
    return PROTECTED


async def _invite(session: AppSession, **fields: Any) -> str:
    return await make_token_invite(session, await make_inviter(session), **fields)


async def _user_id(session: AppSession, username: str) -> UUID:
    return UUID(
        str(
            await scalar(
                session, "SELECT id FROM users WHERE lower(username) = lower(:u)", u=username
            )
        )
    )


# ------------------------------------------------------------------- AC2 success, web


async def test_register_returns_201_and_signs_the_person_in_on_web(
    client: httpx.AsyncClient, app_schema: AppSession, protected: str
) -> None:
    token = await _invite(app_schema)

    response = await register(client, token)

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"access_token", "token_type", "expires_in"}
    assert "refresh_token" not in body
    user_id = await _user_id(app_schema, "newbie")
    assert decode(body["access_token"])["sub"] == str(user_id)
    me = await client.get(protected, headers=bearer(body["access_token"]))
    assert me.status_code == 200
    assert me.json() == {"id": str(user_id)}


async def test_web_register_sets_one_hardened_refresh_cookie_and_it_refreshes(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    token = await _invite(app_schema)

    response = await register(client, token)

    (raw,) = set_cookies(response)
    attributes = cookie_attributes(raw)
    assert "httponly" in attributes
    assert "secure" in attributes
    assert attributes["samesite"].lower() == "strict"
    assert attributes["path"] == COOKIE_PATH
    assert attributes["max-age"] == str(30 * 24 * 3600)
    refresh = cookie_value(response)
    assert refresh not in response.text
    claims = decode(refresh)
    assert claims["type"] == "refresh"
    assert claims["sub"] == str(await _user_id(app_schema, "newbie"))
    refreshed = await post_refresh(client, "web", refresh)
    assert refreshed.status_code == 200
    assert refreshed.json()["access_token"]


async def test_register_stores_the_refresh_token_in_a_new_family(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    token = await _invite(app_schema)
    await register(client, token)
    user_id = await _user_id(app_schema, "newbie")

    (row,) = await rows(app_schema, user_id=user_id)

    assert row.revoked_at is None
    assert row.replaced_by is None


# ------------------------------------------------------------------- AC2 success, mobile


async def test_mobile_register_returns_both_tokens_in_the_body_and_no_cookie(
    client: httpx.AsyncClient, app_schema: AppSession, protected: str
) -> None:
    token = await _invite(app_schema)

    response = await register(client, token, mobile=True)

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"access_token", "token_type", "expires_in", "refresh_token"}
    assert set_cookies(response) == []
    me = await client.get(protected, headers=bearer(body["access_token"]))
    assert me.status_code == 200
    refreshed = await post_refresh(client, "mobile", body["refresh_token"])
    assert refreshed.status_code == 200
    assert refreshed.json()["refresh_token"] != body["refresh_token"]


async def test_x_client_mobile_with_an_origin_is_treated_as_web(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    token = await _invite(app_schema)

    response = await register(client, token, headers={**MOBILE, "Origin": "http://localhost:5173"})

    assert response.status_code == 201
    assert "refresh_token" not in response.json()
    assert len(set_cookies(response)) == 1


async def test_register_response_matches_the_login_response_shape(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    registered = await register(client, await _invite(app_schema), mobile=True)
    logged_in = await client.post(
        LOGIN, json={"username": "newbie", "password": PASSWORD}, headers=MOBILE
    )
    assert set(registered.json()) == set(logged_in.json())
    assert registered.json()["token_type"] == logged_in.json()["token_type"]
    assert registered.json()["expires_in"] == logged_in.json()["expires_in"]


# ------------------------------------------------------------------- AC2 side effects


async def test_register_creates_one_user_and_one_player_with_the_trimmed_display_name(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    token = await _invite(app_schema)
    users, players = await count_users(app_schema), await count_players(app_schema)

    response = await register(client, token, display_name="  Ada L  ")

    assert response.status_code == 201
    assert await count_users(app_schema) == users + 1
    assert await count_players(app_schema) == players + 1
    row = (
        (
            await app_schema.execute(
                text(
                    "SELECT u.username, p.display_name, p.is_guest FROM users u "
                    "JOIN players p ON p.user_id = u.id WHERE u.username = 'newbie'"
                )
            )
        )
        .mappings()
        .one()
    )
    assert dict(row) == {"username": "newbie", "display_name": "Ada L", "is_guest": False}


async def test_register_stores_a_password_hash_that_logs_in(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await register(client, await _invite(app_schema), password="a long enough secret")

    stored = await scalar(app_schema, "SELECT password_hash FROM users WHERE username = 'newbie'")
    login = await client.post(
        LOGIN, json={"username": "newbie", "password": "a long enough secret"}
    )

    assert stored.startswith("$argon2id$")
    assert "a long enough secret" not in stored
    assert login.status_code == 200


async def test_register_increases_the_invite_use_count_by_one(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    token = await _invite(app_schema, max_uses=5)
    assert await use_count_of(app_schema, token) == 0

    await register(client, token)

    assert await use_count_of(app_schema, token) == 1


async def test_an_invite_with_two_uses_registers_two_people_then_gives_410(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    token = await _invite(app_schema, max_uses=2)

    first = await register(client, token, username="first.one")
    second = await register(client, token, username="second.one")
    third = await register(client, token, username="third.one")

    assert (first.status_code, second.status_code) == (201, 201)
    assert_envelope(third, 410, "invite_no_longer_valid")
    assert await use_count_of(app_schema, token) == 2
    assert await scalar(app_schema, "SELECT count(*) FROM users WHERE username = 'third.one'") == 0


# ------------------------------------------------------------------- AC3 email


async def test_a_missing_email_is_stored_as_an_empty_string(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await register(client, await _invite(app_schema))
    assert await scalar(app_schema, "SELECT email FROM users WHERE username = 'newbie'") == ""


async def test_an_email_is_stored_trimmed(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    response = await register(client, await _invite(app_schema), email="  ada@example.com  ")
    assert response.status_code == 201
    assert (
        await scalar(app_schema, "SELECT email FROM users WHERE username = 'newbie'")
        == "ada@example.com"
    )


async def test_the_same_email_can_register_two_users(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    first = await register(
        client, await _invite(app_schema), username="one.user", email="same@example.com"
    )
    second = await register(
        client, await _invite(app_schema), username="two.user", email="same@example.com"
    )
    assert (first.status_code, second.status_code) == (201, 201)
    assert (
        await scalar(app_schema, "SELECT count(*) FROM users WHERE email = 'same@example.com'") == 2
    )


async def test_the_email_cannot_be_used_to_sign_in(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await register(client, await _invite(app_schema), email="ada@example.com")

    login = await client.post(LOGIN, json={"username": "ada@example.com", "password": PASSWORD})

    assert login.status_code == 401


async def test_the_email_is_not_in_the_response(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    response = await register(client, await _invite(app_schema), email="ada@example.com")
    assert "ada@example.com" not in response.text
    assert "email" not in response.json()


async def test_an_email_over_320_characters_is_422_on_the_email_field(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    token = await _invite(app_schema)

    response = await register(client, token, email="a" * 313 + "@abc.com")

    error = assert_envelope(response, 422, "validation_error")
    assert "body.email" in [f["path"] for f in error["details"]["fields"]]
    assert await use_count_of(app_schema, token) == 0


# ------------------------------------------------------------------- invalid invite states


def _state(kind: str) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "revoked": {"revoked_at": now},
        "expired": {"expires_at": now - timedelta(seconds=1)},
        "used_up": {"max_uses": 1, "use_count": 1},
    }[kind]


@pytest.mark.parametrize("kind", ["revoked", "expired", "used_up"])
async def test_an_unusable_invite_gives_410_and_creates_no_user(
    client: httpx.AsyncClient, app_schema: AppSession, kind: str
) -> None:
    token = await _invite(app_schema, **_state(kind))
    users, players = await count_users(app_schema), await count_players(app_schema)
    used = await use_count_of(app_schema, token)

    response = await register(client, token)

    error = assert_envelope(response, 410, "invite_no_longer_valid")
    assert error["message"] == "Invite no longer valid."
    assert set_cookies(response) == []
    assert (await count_users(app_schema), await count_players(app_schema)) == (users, players)
    assert await use_count_of(app_schema, token) == used


async def test_an_unknown_invite_gives_410_and_creates_no_user(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    users, players = await count_users(app_schema), await count_players(app_schema)
    await app_schema.commit()  # keep the schema across the error path's rollback

    response = await register(client, new_token())

    assert_envelope(response, 410, "invite_no_longer_valid")
    assert (await count_users(app_schema), await count_players(app_schema)) == (users, players)


async def test_a_410_on_registration_does_not_reveal_whether_the_username_is_taken(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await create_user_with_player(
        app_schema, username="taken", password=PASSWORD, display_name="Taken"
    )
    await app_schema.commit()
    response = await register(client, new_token(), username="taken")
    assert_envelope(response, 410, "invite_no_longer_valid")


# ------------------------------------------------------------------- AC5 duplicate username


@pytest.mark.parametrize("attempt", ["ada.l", "ADA.L", "Ada.L"])
async def test_a_taken_username_gives_409_in_any_case_and_the_invite_is_not_used(
    client: httpx.AsyncClient, app_schema: AppSession, attempt: str
) -> None:
    await create_user_with_player(
        app_schema, username="Ada.L", password=PASSWORD, display_name="Ada"
    )
    await app_schema.commit()
    token = await _invite(app_schema)
    users, players = await count_users(app_schema), await count_players(app_schema)

    response = await register(client, token, username=attempt)

    assert_envelope(response, 409, "username_taken")
    assert set_cookies(response) == []
    assert await use_count_of(app_schema, token) == 0
    assert (await count_users(app_schema), await count_players(app_schema)) == (users, players)


async def test_after_a_409_the_same_invite_still_registers_someone_else(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await create_user_with_player(
        app_schema, username="taken", password=PASSWORD, display_name="Taken"
    )
    await app_schema.commit()
    token = await _invite(app_schema)

    assert (await register(client, token, username="TAKEN")).status_code == 409
    again = await register(client, token, username="free.name")

    assert again.status_code == 201
    assert await use_count_of(app_schema, token) == 1


async def test_registering_the_same_username_twice_gives_409_the_second_time(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    first = await register(client, await _invite(app_schema), username="Dup.User")
    second_token = await _invite(app_schema)
    second = await register(client, second_token, username="dup.user")

    assert first.status_code == 201
    assert_envelope(second, 409, "username_taken")
    assert await use_count_of(app_schema, second_token) == 0


# ------------------------------------------------------------------- AC6 validation


def _bad_bodies() -> list[tuple[str, dict[str, Any]]]:
    return [
        ("username", {"username": "ab"}),
        ("username", {"username": "a" * 25}),
        ("username", {"username": "bad name"}),
        ("username", {"username": "bad!name"}),
        ("username", {"username": "nanduño"}),
        ("username", {"username": ""}),
        ("password", {"password": "a" * 11}),
        ("password", {"password": "a" * 129}),
        ("password", {"password": ""}),
        ("display_name", {"display_name": ""}),
        ("display_name", {"display_name": "     "}),
        ("display_name", {"display_name": "a" * 33}),
        ("display_name", {"display_name": "  " + "a" * 33 + "  "}),
        ("invite_token", {"invite_token": ""}),
        ("invite_token", {"invite_token": "a" * 129}),  # secrets-check: allow
    ]


@pytest.mark.parametrize(
    ("field", "overrides"),
    _bad_bodies(),
    ids=[f"{f}-{i}" for i, (f, _) in enumerate(_bad_bodies())],
)
async def test_an_invalid_field_gives_422_naming_the_field_and_changes_nothing(
    client: httpx.AsyncClient, app_schema: AppSession, field: str, overrides: dict[str, Any]
) -> None:
    token = await _invite(app_schema)
    users = await count_users(app_schema)
    body_overrides = {k: v for k, v in overrides.items() if k != "invite_token"}

    response = await register(client, overrides.get("invite_token", token), **body_overrides)

    error = assert_envelope(response, 422, "validation_error")
    assert f"body.{field}" in [f["path"] for f in error["details"]["fields"]]
    for secret in ("a" * 129, "a" * 11):
        assert secret not in response.text
    assert await count_users(app_schema) == users
    assert await use_count_of(app_schema, token) == 0


@pytest.mark.parametrize("missing", ["invite_token", "username", "password", "display_name"])
async def test_a_missing_required_field_gives_422_naming_it(
    client: httpx.AsyncClient, app_schema: AppSession, missing: str
) -> None:
    token = await _invite(app_schema)
    body = {
        "invite_token": token,
        "username": "newbie",
        "password": PASSWORD,
        "display_name": "New Bie",
    }
    del body[missing]

    response = await client.post(REGISTER, json=body)

    error = assert_envelope(response, 422, "validation_error")
    assert f"body.{missing}" in [f["path"] for f in error["details"]["fields"]]


@pytest.mark.parametrize(
    "overrides",
    [
        {"username": "abc"},
        {"username": "a" * 24},
        {"username": "Mixed_Case-9.x"},
        {"password": "a" * 12},
        {"password": "a" * 128},
        {"display_name": "a"},
        {"display_name": "a" * 32},
        {"display_name": "  " + "a" * 32 + "  "},
    ],
    ids=["user-3", "user-24", "user-charset", "pw-12", "pw-128", "name-1", "name-32", "name-trim"],
)
async def test_the_boundary_values_are_accepted(
    client: httpx.AsyncClient, app_schema: AppSession, overrides: dict[str, Any]
) -> None:
    response = await register(client, await _invite(app_schema), **overrides)
    assert response.status_code == 201


async def test_a_bad_body_with_an_invalid_invite_gives_422_not_410(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    response = await register(client, new_token(), username="ab")
    assert_envelope(response, 422, "validation_error")


# ------------------------------------------------------------------- logs


async def test_no_log_record_contains_the_token_password_or_email_during_registration(
    client: httpx.AsyncClient, app_schema: AppSession, caplog: pytest.LogCaptureFixture
) -> None:
    token = await _invite(app_schema)
    unknown = new_token()
    caplog.set_level(logging.DEBUG)

    await register(client, token, password="very secret value", email="private@example.com")
    await register(client, unknown, username="other.one")
    await register(client, token, username="third.one")

    for hidden in (token, unknown, "very secret value", "private@example.com"):
        assert hidden not in caplog.text
