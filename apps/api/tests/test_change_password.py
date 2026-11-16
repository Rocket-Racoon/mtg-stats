"""Change my password (S-2.1.8 AC1, AC2, AC3 and the decisions recorded on #67)."""

import sys
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.core import rate_limit
from app.core.db import AppSession
from app.core.ids import uuid7
from app.identity.models import User
from app.identity.services import revoke_all_refresh_tokens
from tests.auth_support import issue
from tests.factories import create
from tests.rate_limit_support import (
    keys_containing,
    keys_under,
    peek_redis,
    spy_on_hashing,
    value_of,
)
from tests.refresh_support import (
    MOBILE,
    PASSWORD,
    Mode,
    RefreshTokenFactory,
    bearer,
    deactivate,
    login_request,
    make_user,
    post_refresh,
    refresh_token_of,
    rows,
    set_cookies,
    sign_in,
)
from tests.register_support import assert_envelope

CHANGE = "/api/v1/auth/change-password"
NEW_PASSWORD = "a brand new passphrase"  # test fixture  # secrets-check: allow
WRONG = "not the password!"
USER_KEY = ":signin:user:"  # key-name fragment, not a secret  # secrets-check: allow
MODES: list[Mode] = ["web", "mobile"]

pytestmark = pytest.mark.usefixtures("app_schema")


@pytest.fixture(autouse=True)
async def _committed_schema(app_schema: AppSession) -> None:
    """Release the savepoint so the app's error-path rollback keeps the schema and the setup."""
    await app_schema.commit()


def change(
    client: httpx.AsyncClient,
    access: str,
    current: str = PASSWORD,
    new: str = NEW_PASSWORD,
    mode: Mode = "mobile",
) -> Any:
    headers = {**bearer(access), **(MOBILE if mode == "mobile" else {})}
    return client.post(
        CHANGE, json={"current_password": current, "new_password": new}, headers=headers
    )


async def can_sign_in(client: httpx.AsyncClient, password: str, username: str = "ada") -> bool:
    response = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}, headers=MOBILE
    )
    return response.status_code == 200


async def hash_of(session: AppSession, user_id: UUID) -> str:
    return (
        await session.execute(select(User.password_hash).where(User.id == user_id))
    ).scalar_one()


def _assert_throttled(response: httpx.Response) -> None:
    assert_envelope(response, 429, "rate_limited")
    retry_after = response.headers["Retry-After"]
    assert retry_after.isdigit()
    assert 1 <= int(retry_after) <= 900


# ------------------------------------------------------------------------------ AC1


@pytest.mark.parametrize("mode", MODES)
async def test_correct_current_password_returns_200_and_the_new_password_signs_in(
    client: httpx.AsyncClient, app_schema: AppSession, mode: Mode
) -> None:
    await make_user(app_schema)
    access, _ = await sign_in(client, mode)

    response = await change(client, access, mode=mode)

    assert response.status_code == 200
    assert await can_sign_in(client, NEW_PASSWORD)
    assert not await can_sign_in(client, PASSWORD)


@pytest.mark.parametrize("mode", MODES)
async def test_the_stored_hash_changes_to_a_hash_of_the_new_password(
    client: httpx.AsyncClient, app_schema: AppSession, mode: Mode
) -> None:
    user = await make_user(app_schema)
    old_hash = await hash_of(app_schema, user.id)
    access, _ = await sign_in(client, mode)

    assert (await change(client, access, mode=mode)).status_code == 200

    new_hash = await hash_of(app_schema, user.id)
    assert new_hash != old_hash
    assert NEW_PASSWORD not in new_hash


@pytest.mark.parametrize("mode", MODES)
async def test_every_refresh_token_the_user_had_is_revoked_and_none_can_refresh(
    client: httpx.AsyncClient, app_schema: AppSession, mode: Mode
) -> None:
    user = await make_user(app_schema)
    sessions = [await sign_in(client, mode) for _ in range(3)]
    before = await rows(app_schema, user_id=user.id)
    assert len(before) == 3
    assert all(r.revoked_at is None for r in before)

    response = await change(client, sessions[0][0], mode=mode)

    assert response.status_code == 200
    old_ids = {r.id for r in before}
    after = await rows(app_schema, user_id=user.id)
    for row in after:
        if row.id in old_ids:
            assert row.revoked_at is not None
    for _, token in sessions:
        assert (await post_refresh(client, mode, token)).status_code == 401


@pytest.mark.parametrize("mode", MODES)
async def test_the_new_refresh_token_starts_a_new_family_and_works_on_refresh(
    client: httpx.AsyncClient, app_schema: AppSession, mode: Mode
) -> None:
    user = await make_user(app_schema)
    access, old = await sign_in(client, mode)
    before = await rows(app_schema, user_id=user.id)

    response = await change(client, access, mode=mode)

    new = refresh_token_of(response, mode)
    assert new != old
    after = await rows(app_schema, user_id=user.id)
    fresh = [r for r in after if r.id not in {b.id for b in before}]
    assert len(fresh) == 1
    assert fresh[0].revoked_at is None
    assert fresh[0].family_id not in {b.family_id for b in before}
    refreshed = await post_refresh(client, mode, new)
    assert refreshed.status_code == 200


@pytest.mark.parametrize("mode", MODES)
async def test_the_new_access_token_works_on_a_protected_route(
    client: httpx.AsyncClient, app_schema: AppSession, mode: Mode
) -> None:
    user = await make_user(app_schema)
    access, _ = await sign_in(client, mode)

    response = await change(client, access, mode=mode)

    me = await client.get("/api/v1/auth/me", headers=bearer(response.json()["access_token"]))
    assert me.status_code == 200
    assert me.json()["id"] == str(user.id)
    assert response.json()["token_type"] == "bearer"
    assert response.json()["expires_in"] == 900


async def test_another_users_refresh_tokens_are_untouched(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_user(app_schema, "ada")
    bob = await make_user(app_schema, "bob")
    access, _ = await sign_in(client, "mobile", "ada")
    _, bob_token = await sign_in(client, "mobile", "bob")
    bob_hash = await hash_of(app_schema, bob.id)

    assert (await change(client, access)).status_code == 200

    bob_rows = await rows(app_schema, user_id=bob.id)
    assert len(bob_rows) == 1
    assert bob_rows[0].revoked_at is None
    assert (await post_refresh(client, "mobile", bob_token)).status_code == 200
    assert await hash_of(app_schema, bob.id) == bob_hash
    assert await can_sign_in(client, PASSWORD, "bob")


async def test_web_gets_the_refresh_cookie_and_no_refresh_token_in_the_body(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_user(app_schema)
    access, _ = await sign_in(client, "web")

    response = await change(client, access, mode="web")

    assert response.status_code == 200
    assert "refresh_token" not in response.json()
    assert len(set_cookies(response)) == 1
    assert refresh_token_of(response, "web")


async def test_mobile_gets_the_refresh_token_in_the_body_and_no_cookie(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_user(app_schema)
    access, _ = await sign_in(client, "mobile")

    response = await change(client, access, mode="mobile")

    assert response.status_code == 200
    assert isinstance(response.json()["refresh_token"], str)
    assert set_cookies(response) == []


async def test_the_response_has_the_same_shape_as_login(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_user(app_schema)
    access, _ = await sign_in(client, "mobile")

    login_body = (await login_request(client, "mobile")).json()
    changed = await change(client, access, mode="mobile")

    assert set(changed.json()) == set(login_body)


# ------------------------------------------------------------------------------ AC2


async def test_a_wrong_current_password_is_400_with_a_field_error_on_current_password(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_user(app_schema)
    access, _ = await sign_in(client, "mobile")

    response = await change(client, access, current=WRONG)

    error = assert_envelope(response, 400, "invalid_current_password")
    assert error["message"] == "Current password is incorrect."
    (field,) = error["details"]["fields"]
    assert field["loc"] == ["body", "current_password"]
    assert field["path"] == "body.current_password"  # same dotted-loc form as a 422
    assert field["message"] == "Current password is incorrect."
    assert "refresh_token" not in response.json()
    assert set_cookies(response) == []


@pytest.mark.parametrize("mode", MODES)
async def test_a_wrong_current_password_changes_nothing(
    client: httpx.AsyncClient, app_schema: AppSession, mode: Mode
) -> None:
    user = await make_user(app_schema)
    access, token = await sign_in(client, mode)
    old_hash = await hash_of(app_schema, user.id)
    before = await rows(app_schema, user_id=user.id)

    response = await change(client, access, current=WRONG, mode=mode)

    assert response.status_code == 400
    assert await hash_of(app_schema, user.id) == old_hash
    assert await rows(app_schema, user_id=user.id) == before
    assert all(r.revoked_at is None for r in before)
    assert await can_sign_in(client, PASSWORD)
    assert not await can_sign_in(client, NEW_PASSWORD)
    assert (await post_refresh(client, mode, token)).status_code == 200


async def test_a_wrong_current_password_with_an_invalid_new_one_is_422_not_400(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_user(app_schema)
    access, _ = await sign_in(client, "mobile")

    response = await change(client, access, current=WRONG, new="short")

    assert_envelope(response, 422, "validation_error")


async def test_the_new_password_equal_to_the_current_one_is_accepted(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_user(app_schema)
    access, _ = await sign_in(client, "mobile")

    response = await change(client, access, new=PASSWORD)

    assert response.status_code == 200
    assert await can_sign_in(client, PASSWORD)


# ------------------------------------------------------------------------------ AC3


@pytest.mark.parametrize("length", [11, 129])
async def test_a_new_password_outside_12_to_128_characters_is_422_on_new_password(
    client: httpx.AsyncClient, app_schema: AppSession, length: int
) -> None:
    user = await make_user(app_schema)
    access, _ = await sign_in(client, "mobile")
    old_hash = await hash_of(app_schema, user.id)
    before = await rows(app_schema, user_id=user.id)

    response = await change(client, access, new="x" * length)

    error = assert_envelope(response, 422, "validation_error")
    assert ["body", "new_password"] in [f["loc"] for f in error["details"]["fields"]]
    assert await hash_of(app_schema, user.id) == old_hash
    assert await rows(app_schema, user_id=user.id) == before
    assert await can_sign_in(client, PASSWORD)


@pytest.mark.parametrize("length", [12, 128])
async def test_a_new_password_of_the_boundary_length_succeeds(
    client: httpx.AsyncClient, app_schema: AppSession, length: int
) -> None:
    await make_user(app_schema)
    access, _ = await sign_in(client, "mobile")
    new = "y" * length

    response = await change(client, access, new=new)

    assert response.status_code == 200
    assert await can_sign_in(client, new)


@pytest.mark.parametrize(
    "body",
    [
        {"new_password": NEW_PASSWORD},
        {"current_password": PASSWORD},
        {"current_password": "", "new_password": NEW_PASSWORD},
        {"current_password": "x" * 129, "new_password": NEW_PASSWORD},  # secrets-check: allow
    ],
)
async def test_a_missing_or_out_of_bounds_field_is_422(
    client: httpx.AsyncClient, app_schema: AppSession, body: dict[str, str]
) -> None:
    await make_user(app_schema)
    access, _ = await sign_in(client, "mobile")

    response = await client.post(CHANGE, json=body, headers=bearer(access))

    assert_envelope(response, 422, "validation_error")


async def test_a_validation_error_does_not_count_against_the_throttle(
    client: httpx.AsyncClient, app_schema: AppSession, rate_limit_prefix: str
) -> None:
    await make_user(app_schema)
    access, _ = await sign_in(client, "mobile")
    for _ in range(6):
        assert (await change(client, access, new="short")).status_code == 422

    assert await keys_containing(rate_limit_prefix, USER_KEY) == []
    assert (await change(client, access)).status_code == 200


# ------------------------------------------------------------------------------ auth


async def test_no_token_is_401_unauthenticated(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    response = await client.post(
        CHANGE, json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    )

    assert_envelope(response, 401, "unauthenticated")


async def test_an_inactive_user_is_401_invalid_token_and_nothing_changes(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    user = await make_user(app_schema)
    access, _ = await sign_in(client, "mobile")
    old_hash = await hash_of(app_schema, user.id)
    await deactivate(app_schema, user)

    response = await change(client, access)

    assert_envelope(response, 401, "invalid_token")
    assert await hash_of(app_schema, user.id) == old_hash


async def test_a_valid_token_of_an_unknown_user_is_401_invalid_token(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    response = await change(client, issue(uuid7()))

    assert_envelope(response, 401, "invalid_token")


async def test_a_garbage_token_is_401_invalid_token(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    assert_envelope(await change(client, "not-a-jwt"), 401, "invalid_token")


async def test_the_user_is_taken_from_the_token_not_the_body(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_user(app_schema, "ada")
    bob = await make_user(app_schema, "bob")
    access, _ = await sign_in(client, "mobile", "ada")
    bob_hash = await hash_of(app_schema, bob.id)

    response = await client.post(
        CHANGE,
        json={
            "current_password": PASSWORD,
            "new_password": NEW_PASSWORD,
            "username": "bob",
            "user_id": str(bob.id),
        },
        headers=bearer(access),
    )

    assert response.status_code == 200
    assert await hash_of(app_schema, bob.id) == bob_hash
    assert await can_sign_in(client, NEW_PASSWORD, "ada")


# ------------------------------------------------------------------------------ throttle


async def test_the_sixth_attempt_after_five_wrong_current_passwords_is_429_even_if_correct(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    user = await make_user(app_schema)
    access, _ = await sign_in(client, "mobile")
    old_hash = await hash_of(app_schema, user.id)
    for _ in range(5):
        assert_envelope(
            await change(client, access, current=WRONG), 400, "invalid_current_password"
        )

    response = await change(client, access)  # the correct password

    _assert_throttled(response)
    assert await hash_of(app_schema, user.id) == old_hash


async def test_a_blocked_change_leaves_the_password_unchanged(
    client: httpx.AsyncClient, app_schema: AppSession, rate_limit_prefix: str
) -> None:
    user = await make_user(app_schema)
    access, _ = await sign_in(client, "mobile")
    old_hash = await hash_of(app_schema, user.id)
    for _ in range(5):
        await change(client, access, current=WRONG)

    _assert_throttled(await change(client, access))

    assert await hash_of(app_schema, user.id) == old_hash
    (user_key,) = await keys_containing(rate_limit_prefix, USER_KEY)
    assert await value_of(user_key) == 5


async def test_failed_sign_ins_and_wrong_changes_share_the_username_counter(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_user(app_schema)
    access, _ = await sign_in(client, "mobile")
    for _ in range(3):
        assert not await can_sign_in(client, WRONG)
    for _ in range(2):
        assert (await change(client, access, current=WRONG)).status_code == 400

    _assert_throttled(await change(client, access))


async def test_four_wrong_changes_leave_the_correct_password_working(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_user(app_schema)
    access, _ = await sign_in(client, "mobile")
    for _ in range(4):
        await change(client, access, current=WRONG)

    assert (await change(client, access)).status_code == 200


async def test_a_successful_change_clears_the_username_counter(
    client: httpx.AsyncClient, app_schema: AppSession, rate_limit_prefix: str
) -> None:
    await make_user(app_schema)
    access, _ = await sign_in(client, "mobile")
    for _ in range(3):
        await change(client, access, current=WRONG)
    assert await keys_containing(rate_limit_prefix, USER_KEY) != []

    response = await change(client, access)

    assert response.status_code == 200
    assert await keys_containing(rate_limit_prefix, USER_KEY) == []


async def test_a_throttled_change_runs_no_password_hashing(
    client: httpx.AsyncClient, app_schema: AppSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_user(app_schema)
    access, _ = await sign_in(client, "mobile")
    for _ in range(5):
        await change(client, access, current=WRONG)
    calls = spy_on_hashing(monkeypatch)

    response = await change(client, access)

    _assert_throttled(response)
    assert calls == []


async def test_the_twentieth_ip_failure_blocks_a_change_from_that_ip(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await make_user(app_schema)
    access, _ = await sign_in(client, "mobile")
    for index in range(20):
        await client.post(
            "/api/v1/auth/login",
            json={"username": f"user{index}", "password": WRONG},
            headers=MOBILE,
        )

    _assert_throttled(await change(client, access))


async def test_a_change_gives_back_the_ip_count_on_success(
    client: httpx.AsyncClient, app_schema: AppSession, rate_limit_prefix: str
) -> None:
    await make_user(app_schema)
    access, _ = await sign_in(client, "mobile")  # a successful sign-in also gives its count back
    ip_keys_before = [k for k in await keys_under(rate_limit_prefix) if ":signin:ip:" in k]
    baseline = [await value_of(k) for k in ip_keys_before]

    assert (await change(client, access)).status_code == 200

    ip_keys = [k for k in await keys_under(rate_limit_prefix) if ":signin:ip:" in k]
    assert [await value_of(k) for k in ip_keys] == baseline


async def test_counter_keys_are_the_same_as_signin_keys_and_carry_a_ttl(
    client: httpx.AsyncClient, app_schema: AppSession, rate_limit_prefix: str
) -> None:
    await make_user(app_schema)
    access, _ = await sign_in(client, "mobile")
    await change(client, access, current=WRONG)

    keys = await keys_under(rate_limit_prefix)

    assert len(keys) == 2
    assert all(k.startswith(f"{rate_limit_prefix}:signin:") for k in keys)
    async with peek_redis() as redis:
        for key in keys:
            assert 1 <= await redis.ttl(key) <= 900


async def test_when_redis_is_down_the_change_succeeds_and_makes_no_extra_redis_calls(
    client: httpx.AsyncClient, app_schema: AppSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_user(app_schema)
    access, _ = await sign_in(client, "mobile")
    reset = AsyncMock()
    give_back = AsyncMock()
    monkeypatch.setattr(
        rate_limit, "acquire", AsyncMock(return_value=rate_limit.Allowed(counted=False))
    )
    monkeypatch.setattr(rate_limit, "reset", reset)
    monkeypatch.setattr(rate_limit, "give_back", give_back)

    response = await change(client, access)

    assert response.status_code == 200
    reset.assert_not_called()
    give_back.assert_not_called()


async def test_a_change_revokes_every_live_token_but_leaves_an_expired_one_untouched(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    user = await make_user(app_schema)
    sessions = [await sign_in(client, "mobile") for _ in range(2)]
    expired = await create(
        app_schema,
        RefreshTokenFactory,
        user_id=user.id,
        expires_at=datetime.now(UTC) - timedelta(days=1),
        revoked_at=None,
    )
    await app_schema.commit()
    live_before = [r.id for r in await rows(app_schema, user_id=user.id) if r.id != expired.id]
    assert len(live_before) == 2

    assert (await change(client, sessions[0][0])).status_code == 200

    after = {r.id: r for r in await rows(app_schema, user_id=user.id)}
    assert after[expired.id].revoked_at is None
    for token_id in live_before:
        assert after[token_id].revoked_at is not None


async def test_revoke_all_refresh_tokens_counts_only_live_unrevoked_tokens(
    app_schema: AppSession,
) -> None:
    user = await make_user(app_schema)
    now = datetime.now(UTC)
    for _ in range(2):
        await create(
            app_schema, RefreshTokenFactory, user_id=user.id, expires_at=now + timedelta(days=1)
        )
    expired = await create(
        app_schema, RefreshTokenFactory, user_id=user.id, expires_at=now - timedelta(seconds=1)
    )
    await create(
        app_schema,
        RefreshTokenFactory,
        user_id=user.id,
        expires_at=now + timedelta(days=1),
        revoked_at=now,
    )

    revoked = await revoke_all_refresh_tokens(app_schema, user_id=user.id)

    assert revoked == 2
    after = {r.id: r for r in await rows(app_schema, user_id=user.id)}
    assert after[expired.id].revoked_at is None


# ------------------------------------------------------------------------------ robustness


def _patch_start_session(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("database exploded")

    for name, module in list(sys.modules.items()):
        if name.startswith("app.identity") and hasattr(module, "start_session"):
            monkeypatch.setattr(module, "start_session", boom)


async def test_an_error_after_verification_rolls_back_the_hash_and_the_revocation(
    client: httpx.AsyncClient,
    app_schema: AppSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await make_user(app_schema)
    sessions = [await sign_in(client, "mobile") for _ in range(2)]
    old_hash = await hash_of(app_schema, user.id)
    before = await rows(app_schema, user_id=user.id)
    _patch_start_session(monkeypatch)

    response = await change(client, sessions[0][0])

    assert response.status_code == 500
    assert await hash_of(app_schema, user.id) == old_hash
    assert await rows(app_schema, user_id=user.id) == before
    assert all(r.revoked_at is None for r in before)
    monkeypatch.undo()
    assert await can_sign_in(client, PASSWORD)
    assert not await can_sign_in(client, NEW_PASSWORD)


async def test_an_unexpected_error_does_not_leave_a_counted_attempt(
    client: httpx.AsyncClient,
    app_schema: AppSession,
    rate_limit_prefix: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await make_user(app_schema)
    access, _ = await sign_in(client, "mobile")
    _patch_start_session(monkeypatch)

    assert (await change(client, access)).status_code == 500

    for key in await keys_under(rate_limit_prefix):
        assert await value_of(key) == 0


# ------------------------------------------------------------------------------ OpenAPI


async def test_the_route_is_a_post_with_the_identity_operation_id_and_a_bearer_requirement(
    api_app: FastAPI,
) -> None:
    operations = api_app.openapi()["paths"][CHANGE]

    assert set(operations) == {"post"}
    operation = operations["post"]
    assert operation["operationId"] == "identity_change_password"
    assert operation["tags"] == ["auth"]
    assert operation["security"], "the route must advertise the bearer scheme"


async def test_the_request_schema_has_exactly_the_two_password_fields(api_app: FastAPI) -> None:
    spec = api_app.openapi()
    ref = spec["paths"][CHANGE]["post"]["requestBody"]["content"]["application/json"]["schema"][
        "$ref"
    ]
    schema = spec["components"]["schemas"][ref.rsplit("/", 1)[-1]]

    assert set(schema["properties"]) == {"current_password", "new_password"}
    assert set(schema["required"]) == {"current_password", "new_password"}
