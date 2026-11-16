"""``POST /invites``, ``GET /invites`` and ``POST /invites/{id}/revoke`` (S-2.1.2 AC1, 2, 3, 5)."""

import base64
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest

from app.core.db import AppSession
from app.core.ids import uuid7
from tests.invite_support import (
    INVITES,
    create_via_api,
    make_account,
    make_invite,
    random_bytes_of,
    raw_rows,
    sha256_hex,
    token_from_url,
)
from tests.statement_counter import StatementCounter

pytestmark = pytest.mark.usefixtures("app_schema")
WEB = "http://localhost:5173"  # forced PUBLIC_WEB_URL in conftest
ITEM_KEYS = {"id", "status", "expires_at", "max_uses", "use_count", "revoked_at", "created_at"}
SLACK = timedelta(seconds=5)


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


# ------------------------------------------------------------------------------ AC1 create


async def test_site_admin_creates_an_invite_with_the_defaults(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, headers = await make_account(app_schema, admin=True)
    before = datetime.now(UTC)

    response = await create_via_api(client, headers)

    after = datetime.now(UTC)
    assert response.status_code == 201
    body = response.json()
    assert body["max_uses"] == 1
    assert body["use_count"] == 0
    assert body["status"] == "active"
    assert body["revoked_at"] is None
    expires = _dt(body["expires_at"])
    assert before + timedelta(days=7) - timedelta(seconds=2) <= expires
    assert expires <= after + timedelta(days=7) + timedelta(seconds=2)


async def test_the_invite_url_is_an_https_web_link_with_a_32_byte_token(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, headers = await make_account(app_schema, admin=True)

    body = (await create_via_api(client, headers)).json()

    assert body["url"].startswith(f"{WEB}/invite/")
    assert "mtgstats://" not in str(body)
    token = token_from_url(body["url"], WEB)
    assert "/" not in token
    assert random_bytes_of(token) >= 32


async def test_each_invite_gets_a_different_token(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, headers = await make_account(app_schema, admin=True)
    urls = {(await create_via_api(client, headers)).json()["url"] for _ in range(3)}
    assert len(urls) == 3


@pytest.mark.parametrize("body", [None, {}])
async def test_an_empty_or_missing_body_uses_the_defaults(
    client: httpx.AsyncClient, app_schema: AppSession, body: dict[str, Any] | None
) -> None:
    _, headers = await make_account(app_schema, admin=True)
    response = await create_via_api(client, headers, body)
    assert response.status_code == 201
    assert response.json()["max_uses"] == 1


async def test_the_201_response_has_the_item_shape_plus_url_and_no_separate_token(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, headers = await make_account(app_schema, admin=True)
    body = (await create_via_api(client, headers)).json()
    assert set(body) == ITEM_KEYS | {"url"}
    token = token_from_url(body["url"], WEB)
    assert all(token not in str(v) for k, v in body.items() if k != "url")
    UUID(body["id"])


# ------------------------------------------------------------------------------ AC2 values


async def test_custom_expiry_and_max_uses_are_used(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, headers = await make_account(app_schema, admin=True)
    before = datetime.now(UTC)

    response = await create_via_api(client, headers, {"expires_in_days": 12, "max_uses": 7})

    assert response.status_code == 201
    body = response.json()
    assert body["max_uses"] == 7
    assert abs(_dt(body["expires_at"]) - (before + timedelta(days=12))) < SLACK


@pytest.mark.parametrize(("days", "uses"), [(1, 1), (30, 20), (1, 20), (30, 1)])
async def test_both_edges_of_each_range_are_accepted(
    client: httpx.AsyncClient, app_schema: AppSession, days: int, uses: int
) -> None:
    _, headers = await make_account(app_schema, admin=True)
    before = datetime.now(UTC)
    response = await create_via_api(client, headers, {"expires_in_days": days, "max_uses": uses})
    assert response.status_code == 201
    body = response.json()
    assert body["max_uses"] == uses
    assert abs(_dt(body["expires_at"]) - (before + timedelta(days=days))) < SLACK


async def test_only_one_field_given_keeps_the_default_for_the_other(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, headers = await make_account(app_schema, admin=True)
    before = datetime.now(UTC)
    only_days = (await create_via_api(client, headers, {"expires_in_days": 2})).json()
    only_uses = (await create_via_api(client, headers, {"max_uses": 5})).json()
    assert only_days["max_uses"] == 1
    assert abs(_dt(only_days["expires_at"]) - (before + timedelta(days=2))) < SLACK
    assert only_uses["max_uses"] == 5
    assert abs(_dt(only_uses["expires_at"]) - (before + timedelta(days=7))) < SLACK


@pytest.mark.parametrize(
    "body",
    [
        {"expires_in_days": 0},
        {"expires_in_days": 31},
        {"expires_in_days": -1},
        {"expires_in_days": "abc"},
        {"expires_in_days": 1.5},
        {"max_uses": 0},
        {"max_uses": 21},
        {"max_uses": -1},
        {"max_uses": "many"},
        {"max_uses": 2.5},
    ],
)
async def test_out_of_range_or_non_integer_values_return_422(
    client: httpx.AsyncClient, app_schema: AppSession, body: dict[str, Any]
) -> None:
    _, headers = await make_account(app_schema, admin=True)

    response = await create_via_api(client, headers, body)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert await raw_rows(app_schema) == []


# ------------------------------------------------------------------------------ AC3 storage


async def test_only_the_sha256_of_the_token_is_stored(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, headers = await make_account(app_schema, admin=True)
    body = (await create_via_api(client, headers)).json()
    token = token_from_url(body["url"], WEB)

    (row,) = await raw_rows(app_schema)

    assert row["token_hash"] == sha256_hex(token)
    assert len(row["token_hash"]) == 64
    for column, value in row.items():
        assert token not in str(value), f"token text found in column {column}"
    assert str(row["id"]) == body["id"]
    assert base64.urlsafe_b64encode(token.encode()).decode() not in str(row)


# ------------------------------------------------------------------------------ AC5 permission


async def test_a_signed_in_non_admin_gets_403_forbidden_and_nothing_is_created(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, headers = await make_account(app_schema, admin=False)

    response = await create_via_api(client, headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert await raw_rows(app_schema) == []


async def test_creating_without_a_token_is_401(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    response = await client.post(INVITES)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


async def test_an_inactive_site_admin_cannot_create(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, headers = await make_account(app_schema, admin=True, active=False)
    response = await create_via_api(client, headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_listing_and_revoking_need_a_signed_in_user(client: httpx.AsyncClient) -> None:
    listed = await client.get(INVITES)
    revoked = await client.post(f"{INVITES}/{uuid7()}/revoke")
    assert (listed.status_code, revoked.status_code) == (401, 401)
    assert listed.json()["error"]["code"] == "unauthenticated"
    assert revoked.json()["error"]["code"] == "unauthenticated"


# ------------------------------------------------------------------------------ GET /invites


async def test_a_non_admin_can_list_their_own_invites(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    user_id, headers = await make_account(app_schema, admin=False)
    invite_id = await make_invite(app_schema, user_id)

    response = await client.get(INVITES, headers=headers)

    assert response.status_code == 200
    assert [i["id"] for i in response.json()["items"]] == [str(invite_id)]


async def test_the_list_returns_only_the_callers_invites(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    ada, ada_headers = await make_account(app_schema, admin=True)
    bob, bob_headers = await make_account(app_schema, admin=True)
    mine = await make_invite(app_schema, ada)
    theirs = await make_invite(app_schema, bob)

    ada_list = (await client.get(INVITES, headers=ada_headers)).json()
    bob_list = (await client.get(INVITES, headers=bob_headers)).json()

    assert [i["id"] for i in ada_list["items"]] == [str(mine)]
    assert [i["id"] for i in bob_list["items"]] == [str(theirs)]
    assert ada_list["next_cursor"] is None


async def test_the_list_is_newest_first(client: httpx.AsyncClient, app_schema: AppSession) -> None:
    user_id, headers = await make_account(app_schema, admin=True)
    now = datetime.now(UTC)
    ids = [
        await make_invite(app_schema, user_id, created_at=now - timedelta(hours=h))
        for h in (3, 1, 2)
    ]

    items = (await client.get(INVITES, headers=headers)).json()["items"]

    assert [i["id"] for i in items] == [str(ids[1]), str(ids[2]), str(ids[0])]


async def test_each_state_is_reported_with_its_status(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    user_id, headers = await make_account(app_schema, admin=True)
    now = datetime.now(UTC)
    day = timedelta(days=1)
    active = await make_invite(app_schema, user_id, created_at=now - 6 * day)
    used = await make_invite(app_schema, user_id, max_uses=2, use_count=2, created_at=now - 5 * day)
    expired = await make_invite(app_schema, user_id, expires_at=now - day, created_at=now - 4 * day)
    revoked = await make_invite(app_schema, user_id, revoked_at=now - day, created_at=now - 3 * day)
    # Precedence: revoked beats used and expired; used beats expired.
    revoked_and_used = await make_invite(
        app_schema, user_id, revoked_at=now, use_count=1, created_at=now - 2 * day
    )
    used_and_expired = await make_invite(
        app_schema, user_id, use_count=1, expires_at=now - day, created_at=now - day
    )

    items = {i["id"]: i for i in (await client.get(INVITES, headers=headers)).json()["items"]}

    assert items[str(active)]["status"] == "active"
    assert items[str(used)]["status"] == "used"
    assert items[str(expired)]["status"] == "expired"
    assert items[str(revoked)]["status"] == "revoked"
    assert items[str(revoked_and_used)]["status"] == "revoked"
    assert items[str(used_and_expired)]["status"] == "used"
    assert items[str(revoked)]["revoked_at"] is not None
    assert items[str(active)]["revoked_at"] is None
    assert items[str(used)]["use_count"] == 2
    assert items[str(used)]["max_uses"] == 2


async def test_an_invite_expiring_exactly_now_is_listed_as_expired(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    user_id, headers = await make_account(app_schema, admin=True)
    invite = await make_invite(app_schema, user_id, expires_at=datetime.now(UTC))
    (item,) = (await client.get(INVITES, headers=headers)).json()["items"]
    assert item["id"] == str(invite)
    assert item["status"] == "expired"


async def test_list_items_have_the_item_shape_and_never_a_token_or_url(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, headers = await make_account(app_schema, admin=True)
    created = (await create_via_api(client, headers)).json()
    token = token_from_url(created["url"], WEB)

    response = await client.get(INVITES, headers=headers)

    (item,) = response.json()["items"]
    assert set(item) == ITEM_KEYS
    assert token not in response.text
    assert sha256_hex(token) not in response.text
    assert set(response.json()) == {"items", "next_cursor"}


async def test_paging_walks_every_invite_once_newest_first(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    user_id, headers = await make_account(app_schema, admin=True)
    now = datetime.now(UTC)
    ids = [
        await make_invite(app_schema, user_id, created_at=now - timedelta(minutes=m))
        for m in range(5)
    ]  # ids[0] is the newest

    first = (await client.get(INVITES, params={"limit": 2}, headers=headers)).json()
    assert [i["id"] for i in first["items"]] == [str(ids[0]), str(ids[1])]
    assert first["next_cursor"] is not None
    second = (
        await client.get(
            INVITES, params={"limit": 2, "cursor": first["next_cursor"]}, headers=headers
        )
    ).json()
    assert [i["id"] for i in second["items"]] == [str(ids[2]), str(ids[3])]
    third = (
        await client.get(
            INVITES, params={"limit": 2, "cursor": second["next_cursor"]}, headers=headers
        )
    ).json()
    assert [i["id"] for i in third["items"]] == [str(ids[4])]
    assert third["next_cursor"] is None


async def test_limit_defaults_to_50_and_is_capped_at_200(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    user_id, headers = await make_account(app_schema, admin=True)
    for _ in range(51):
        await make_invite(app_schema, user_id)

    default = (await client.get(INVITES, headers=headers)).json()
    assert len(default["items"]) == 50
    assert default["next_cursor"] is not None
    for bad in (0, 201):
        response = await client.get(INVITES, params={"limit": bad}, headers=headers)
        assert response.status_code == 422
    assert (await client.get(INVITES, params={"limit": 200}, headers=headers)).status_code == 200


async def test_a_malformed_cursor_is_422_invalid_cursor(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, headers = await make_account(app_schema, admin=True)
    response = await client.get(INVITES, params={"cursor": "not-a-cursor"}, headers=headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_cursor"


async def test_listing_runs_the_same_number_of_statements_for_1_and_6_invites(
    client: httpx.AsyncClient,
    app_schema: AppSession,
    count_statements: Callable[[], AbstractContextManager[StatementCounter]],
) -> None:
    user_id, headers = await make_account(app_schema, admin=True)
    await make_invite(app_schema, user_id)
    with count_statements() as small:
        assert (await client.get(INVITES, headers=headers)).status_code == 200
    for _ in range(5):
        await make_invite(app_schema, user_id)
    with count_statements() as large:
        response = await client.get(INVITES, headers=headers)
    assert len(response.json()["items"]) == 6
    assert large.count == small.count


# ------------------------------------------------------------------------------ revoke


async def test_the_creator_revokes_their_invite_and_gets_it_back_as_revoked(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, headers = await make_account(app_schema, admin=True)
    created = (await create_via_api(client, headers)).json()
    before = datetime.now(UTC)

    response = await client.post(f"{INVITES}/{created['id']}/revoke", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == ITEM_KEYS
    assert body["id"] == created["id"]
    assert body["status"] == "revoked"
    assert abs(_dt(body["revoked_at"]) - before) < SLACK
    (listed,) = (await client.get(INVITES, headers=headers)).json()["items"]
    assert listed["status"] == "revoked"


async def test_revoking_twice_is_idempotent_and_keeps_the_first_revoked_at(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, headers = await make_account(app_schema, admin=True)
    created = (await create_via_api(client, headers)).json()
    url = f"{INVITES}/{created['id']}/revoke"

    first = await client.post(url, headers=headers)
    second = await client.post(url, headers=headers)

    assert (first.status_code, second.status_code) == (200, 200)
    assert second.json() == first.json()


async def test_a_used_or_expired_invite_can_still_be_revoked(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    user_id, headers = await make_account(app_schema, admin=True)
    used = await make_invite(app_schema, user_id, use_count=1)
    expired = await make_invite(
        app_schema, user_id, expires_at=datetime.now(UTC) - timedelta(days=1)
    )
    for invite_id in (used, expired):
        response = await client.post(f"{INVITES}/{invite_id}/revoke", headers=headers)
        assert response.status_code == 200
        assert response.json()["status"] == "revoked"


async def test_a_non_admin_creator_can_revoke_their_own_invite(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    user_id, headers = await make_account(app_schema, admin=False)
    invite_id = await make_invite(app_schema, user_id)
    response = await client.post(f"{INVITES}/{invite_id}/revoke", headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "revoked"


async def test_revoking_another_users_invite_is_404_not_found_and_changes_nothing(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    owner, _ = await make_account(app_schema, admin=True)
    _, intruder = await make_account(app_schema, admin=True)
    invite_id = await make_invite(app_schema, owner)

    response = await client.post(f"{INVITES}/{invite_id}/revoke", headers=intruder)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    (row,) = await raw_rows(app_schema)
    assert row["revoked_at"] is None


@pytest.mark.parametrize("invite_id", [str(uuid7()), "not-a-uuid", "123"])
async def test_revoking_an_unknown_or_malformed_id_is_404_not_found(
    client: httpx.AsyncClient, app_schema: AppSession, invite_id: str
) -> None:
    _, headers = await make_account(app_schema, admin=True)
    response = await client.post(f"{INVITES}/{invite_id}/revoke", headers=headers)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
