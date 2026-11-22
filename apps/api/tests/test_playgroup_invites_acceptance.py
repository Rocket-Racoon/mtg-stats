"""Acceptance tests for S-2.4.2 (invite people to a playgroup), API part, from the criteria.

AC1 owner/admin create an invite bound to the playgroup and its preview shows the name, AC2 a new
person registering with it becomes a member, AC3 an existing user confirms and joins (use count up;
an existing member is told ``already_member`` and no use is counted), AC4 a plain member gets 403.
The S-2.4.1 404 rules, paging, query count, revoke and the Phase 0 routes are re-checked. AC5 is
mobile. The race is in ``test_playgroup_invites_acceptance_concurrency.py``.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import AppSession
from tests.invite_support import make_account, token_from_url
from tests.playgroup_support import join, make_user
from tests.rate_limit_support import use_settings
from tests.register_support import (
    PREVIEW,
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
from tests.statement_counter import StatementCounter

pytestmark = pytest.mark.usefixtures("app_schema")
GROUPS = "/api/v1/playgroups"
INVITES = "/api/v1/invites"
JOIN = f"{INVITES}/join"
Headers = dict[str, str]
PREVIEW_KEYS = {"inviter_display_name", "expires_at", "playgroup_name"}


async def _group(
    client: httpx.AsyncClient, headers: Headers, name: str = "Friday", description: str = ""
) -> UUID:
    response = await client.post(
        GROUPS, json={"name": name, "description": description}, headers=headers
    )
    assert response.status_code == 201
    return UUID(response.json()["id"])


async def _create(
    client: httpx.AsyncClient,
    group_id: object,
    headers: Headers,
    body: dict[str, Any] | None = None,
) -> httpx.Response:
    if body is None:
        return await client.post(f"{GROUPS}/{group_id}/invites", headers=headers)
    return await client.post(f"{GROUPS}/{group_id}/invites", json=body, headers=headers)


async def _make(
    client: httpx.AsyncClient, group_id: object, headers: Headers, **body: Any
) -> tuple[str, dict[str, Any]]:
    """Create through the API; return ``(token taken from the url, response body)``."""
    response = await _create(client, group_id, headers, body or None)
    assert response.status_code == 201
    data: dict[str, Any] = response.json()
    return token_from_url(data["url"], get_settings().public_web_url), data


async def _preview(client: httpx.AsyncClient, token: str) -> httpx.Response:
    return await client.post(PREVIEW, json={"token": token})  # no Authorization header


async def _join(client: httpx.AsyncClient, headers: Headers, token: str) -> httpx.Response:
    return await client.post(JOIN, json={"token": token}, headers=headers)


async def _my_groups(client: httpx.AsyncClient, headers: Headers) -> list[dict[str, Any]]:
    response = await client.get(GROUPS, headers=headers)
    assert response.status_code == 200
    items: list[dict[str, Any]] = response.json()["items"]
    return items


async def _memberships(session: AppSession, group_id: UUID, player_id: UUID) -> list[Any]:
    rows = await session.execute(
        text(
            "SELECT role, left_at FROM memberships WHERE playgroup_id = :g AND player_id = :p "
            "ORDER BY joined_at, id"
        ),
        {"g": group_id, "p": player_id},
    )
    return list(rows)


async def _uses(session: AppSession, invite_id: object) -> int:
    return int(await scalar(session, "SELECT use_count FROM invites WHERE id = :i", i=invite_id))


async def _invite_count(session: AppSession) -> int:
    return int(await scalar(session, "SELECT count(*) FROM invites"))


async def _member_count(session: AppSession, group_id: UUID) -> int:
    return int(
        await scalar(
            session,
            "SELECT count(*) FROM memberships WHERE playgroup_id = :g AND left_at IS NULL",
            g=group_id,
        )
    )


async def _owner_and_group(
    client: httpx.AsyncClient, session: AppSession, **kwargs: str
) -> tuple[UUID, UUID, UUID, Headers]:
    """``(owner user id, owner player id, playgroup id, owner headers)``."""
    user_id, player_id, headers = await make_user(session)
    return user_id, player_id, await _group(client, headers, **kwargs), headers


async def _dead_tokens(session: AppSession, group_id: UUID, created_by: UUID) -> dict[str, str]:
    now = datetime.now(UTC)
    return {
        "revoked": await make_token_invite(
            session, created_by, playgroup_id=group_id, revoked_at=now
        ),
        "expired": await make_token_invite(
            session, created_by, playgroup_id=group_id, expires_at=now - timedelta(seconds=5)
        ),
        "used_up": await make_token_invite(
            session, created_by, playgroup_id=group_id, max_uses=2, use_count=2
        ),
        "unknown": new_token(),
    }


# ------------------------------------------------------------------------------------------ AC1


@pytest.mark.parametrize("role", ["owner", "admin"])
async def test_owner_and_admin_create_an_invite_bound_to_the_playgroup(
    client: httpx.AsyncClient, app_schema: AppSession, role: str
) -> None:
    _, _, group_id, owner = await _owner_and_group(client, app_schema)
    _, admin_player, admin = await make_user(app_schema)
    await join(app_schema, group_id, admin_player, "admin")
    headers = owner if role == "owner" else admin

    response = await _create(client, group_id, headers)

    assert response.status_code == 201
    body = response.json()
    assert body["playgroup_id"] == str(group_id)
    assert body["status"] == "active"
    in_db = await scalar(app_schema, "SELECT playgroup_id FROM invites WHERE id = :i", i=body["id"])
    assert str(in_db) == str(group_id)


async def test_the_invite_link_is_an_https_web_url_carrying_the_token(
    client: httpx.AsyncClient,
    app_schema: AppSession,
    api_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_settings(api_app, monkeypatch, PUBLIC_WEB_URL="https://mtg.example.test")
    _, _, group_id, owner = await _owner_and_group(client, app_schema)

    token, body = await _make(client, group_id, owner)

    assert body["url"] == f"https://mtg.example.test/invite/{token}"
    assert (await _preview(client, token)).status_code == 200


async def test_the_invite_link_is_built_on_the_configured_web_url_not_the_request_host(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, group_id, owner = await _owner_and_group(client, app_schema)

    response = await client.post(
        f"{GROUPS}/{group_id}/invites",
        headers={**owner, "Host": "evil.example", "Origin": "http://evil.example"},
    )

    assert response.json()["url"].startswith(get_settings().public_web_url + "/invite/")


async def test_the_raw_token_appears_nowhere_after_creation(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, group_id, owner = await _owner_and_group(client, app_schema)
    token, body = await _make(client, group_id, owner)

    listing = await client.get(f"{GROUPS}/{group_id}/invites", headers=owner)

    assert token not in listing.text
    assert "url" not in listing.json()["items"][0]
    assert "token" not in listing.json()["items"][0]
    assert body["id"] == listing.json()["items"][0]["id"]
    columns = await app_schema.execute(text("SELECT * FROM invites"))
    assert token not in repr([dict(r) for r in columns.mappings()])


async def test_an_anonymous_preview_shows_the_playgroup_name(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, group_id, owner = await _owner_and_group(client, app_schema, name="Friday Night Magic")
    token, _ = await _make(client, group_id, owner)

    response = await _preview(client, token)

    assert response.status_code == 200
    assert response.json()["playgroup_name"] == "Friday Night Magic"


async def test_the_preview_reveals_only_the_name_the_inviter_and_the_expiry(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, group_id, owner = await _owner_and_group(
        client, app_schema, name="Friday", description="a very private description"
    )
    _, member_player, _ = await make_user(app_schema)
    await app_schema.execute(
        text("UPDATE players SET display_name = 'Zebulon Quux' WHERE id = :p"), {"p": member_player}
    )
    await app_schema.commit()
    await join(app_schema, group_id, member_player)
    token, _ = await _make(client, group_id, owner)

    response = await _preview(client, token)

    assert set(response.json()) == PREVIEW_KEYS
    for secret in (str(group_id), "a very private description", "Zebulon", str(member_player)):
        assert secret not in response.text


async def test_renaming_the_playgroup_changes_what_a_later_preview_shows(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, group_id, owner = await _owner_and_group(client, app_schema, name="Before")
    token, _ = await _make(client, group_id, owner)
    assert (await _preview(client, token)).json()["playgroup_name"] == "Before"

    renamed = await client.patch(f"{GROUPS}/{group_id}", json={"name": "After"}, headers=owner)

    assert renamed.status_code == 200
    assert (await _preview(client, token)).json()["playgroup_name"] == "After"


async def test_a_site_level_invite_preview_has_no_playgroup_name(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    token = await make_token_invite(app_schema, await make_inviter(app_schema))

    response = await _preview(client, token)

    assert response.status_code == 200
    assert set(response.json()) == PREVIEW_KEYS
    assert response.json()["playgroup_name"] is None


async def test_creating_with_limits_stores_them_and_validates_the_ranges(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, group_id, owner = await _owner_and_group(client, app_schema)
    default = (await _create(client, group_id, owner)).json()
    custom = (
        await _create(client, group_id, owner, {"expires_in_days": 30, "max_uses": 20})
    ).json()

    assert default["max_uses"] == 1
    assert default["use_count"] == 0
    expires = datetime.fromisoformat(default["expires_at"])
    assert timedelta(days=6, hours=23) < expires - datetime.now(UTC) <= timedelta(days=7, minutes=1)
    assert custom["max_uses"] == 20
    for bad in (
        {"expires_in_days": 0},
        {"expires_in_days": 31},
        {"max_uses": 0},
        {"max_uses": 21},
    ):
        assert (await _create(client, group_id, owner, bad)).status_code == 422
    assert await _invite_count(app_schema) == 2


# ------------------------------------------------------------------------------------------ AC2


async def test_registering_with_a_playgroup_invite_makes_the_new_user_a_member(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, group_id, owner = await _owner_and_group(client, app_schema, name="Friday")
    token, invite = await _make(client, group_id, owner)

    response = await register(client, token)

    assert response.status_code == 201
    bearer = {"Authorization": f"Bearer {response.json()['access_token']}"}
    groups = await _my_groups(client, bearer)
    assert [(g["id"], g["name"], g["role"]) for g in groups] == [
        (str(group_id), "Friday", "member")
    ]
    rows = await app_schema.execute(
        text(
            "SELECT m.role, m.left_at FROM memberships m JOIN players p ON p.id = m.player_id "
            "JOIN users u ON u.id = p.user_id WHERE u.username = 'newbie' AND m.playgroup_id = :g"
        ),
        {"g": group_id},
    )
    assert [(r[0], r[1]) for r in rows] == [("member", None)]
    assert await _uses(app_schema, invite["id"]) == 1


async def test_registering_with_a_multi_use_invite_counts_one_use_each_time(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, group_id, owner = await _owner_and_group(client, app_schema)
    token, invite = await _make(client, group_id, owner, max_uses=3)

    for name in ("first", "second"):
        assert (await register(client, token, username=name)).status_code == 201

    assert await _uses(app_schema, invite["id"]) == 2
    assert await _member_count(app_schema, group_id) == 3  # the owner plus two


@pytest.mark.parametrize(
    ("overrides", "status", "code"),
    [
        ({"username": "taken"}, 409, "username_taken"),
        ({"password": "short"}, 422, "validation_error"),
        ({"username": "bad name!"}, 422, "validation_error"),
    ],
)
async def test_a_failed_registration_creates_no_user_no_membership_and_counts_no_use(
    client: httpx.AsyncClient,
    app_schema: AppSession,
    overrides: dict[str, str],
    status: int,
    code: str,
) -> None:
    _, _, group_id, owner = await _owner_and_group(client, app_schema)
    token, invite = await _make(client, group_id, owner, max_uses=5)
    assert (await register(client, token, username="taken")).status_code == 201
    users, players = await count_users(app_schema), await count_players(app_schema)
    members = await _member_count(app_schema, group_id)

    response = await register(client, token, **overrides)

    assert_envelope(response, status, code)
    assert await count_users(app_schema) == users
    assert await count_players(app_schema) == players
    assert await _member_count(app_schema, group_id) == members
    assert await _uses(app_schema, invite["id"]) == 1


async def test_registering_with_a_site_level_invite_still_works_and_joins_no_playgroup(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    token = await make_token_invite(app_schema, await make_inviter(app_schema))

    response = await register(client, token)

    assert response.status_code == 201
    bearer = {"Authorization": f"Bearer {response.json()['access_token']}"}
    assert await _my_groups(client, bearer) == []
    assert await use_count_of(app_schema, token) == 1
    assert int(await scalar(app_schema, "SELECT count(*) FROM memberships")) == 0


async def test_a_single_use_playgroup_invite_cannot_register_a_second_person(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, group_id, owner = await _owner_and_group(client, app_schema)
    token, invite = await _make(client, group_id, owner)
    assert (await register(client, token, username="first")).status_code == 201
    users, members = await count_users(app_schema), await _member_count(app_schema, group_id)

    second = await register(client, token, username="second")

    assert_envelope(second, 410, "invite_no_longer_valid")
    assert await count_users(app_schema) == users
    assert await _member_count(app_schema, group_id) == members
    assert await _uses(app_schema, invite["id"]) == 1


# ------------------------------------------------------------------------------------------ AC3


async def test_a_signed_in_non_member_joins_and_the_use_count_goes_up_by_one(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, group_id, owner = await _owner_and_group(client, app_schema, name="Friday")
    _, joiner_player, joiner = await make_user(app_schema)
    token, invite = await _make(client, group_id, owner, max_uses=5)

    response = await _join(client, joiner, token)

    assert response.status_code == 200
    body = response.json()
    assert body["already_member"] is False
    assert body["playgroup"]["id"] == str(group_id)
    assert body["playgroup"]["name"] == "Friday"
    assert body["playgroup"]["role"] == "member"
    assert [(r[0], r[1]) for r in await _memberships(app_schema, group_id, joiner_player)] == [
        ("member", None)
    ]
    assert await _uses(app_schema, invite["id"]) == 1
    assert [g["id"] for g in await _my_groups(client, joiner)] == [str(group_id)]


@pytest.mark.parametrize("role", ["member", "admin", "owner"])
async def test_an_existing_member_is_told_already_member_and_no_use_is_counted(
    client: httpx.AsyncClient, app_schema: AppSession, role: str
) -> None:
    _, owner_player, group_id, owner = await _owner_and_group(client, app_schema, name="Friday")
    _, other_player, other = await make_user(app_schema)
    if role != "owner":
        await join(app_schema, group_id, other_player, role)
    headers, player = (owner, owner_player) if role == "owner" else (other, other_player)
    token, invite = await _make(client, group_id, owner, max_uses=5)

    response = await _join(client, headers, token)

    assert response.status_code == 200
    assert response.json()["already_member"] is True
    assert response.json()["playgroup"]["id"] == str(group_id)
    assert response.json()["playgroup"]["name"] == "Friday"
    assert response.json()["playgroup"]["role"] == role
    assert await _uses(app_schema, invite["id"]) == 0
    assert len(await _memberships(app_schema, group_id, player)) == 1


async def test_joining_twice_counts_one_use_and_the_second_answer_is_already_member(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, group_id, owner = await _owner_and_group(client, app_schema)
    _, joiner_player, joiner = await make_user(app_schema)
    token, invite = await _make(client, group_id, owner, max_uses=5)

    first = await _join(client, joiner, token)
    second = await _join(client, joiner, token)

    assert (first.json()["already_member"], second.json()["already_member"]) == (False, True)
    assert await _uses(app_schema, invite["id"]) == 1
    assert len(await _memberships(app_schema, group_id, joiner_player)) == 1


async def test_a_user_in_many_playgroups_can_still_join_another(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, group_id, owner = await _owner_and_group(client, app_schema)
    _, _, joiner = await make_user(app_schema)
    for i in range(12):
        await _group(client, joiner, name=f"Own {i}")
    token, _ = await _make(client, group_id, owner)

    response = await _join(client, joiner, token)

    assert response.status_code == 200
    assert response.json()["already_member"] is False
    assert len(await _my_groups(client, joiner)) == 13


async def test_a_former_member_can_join_again_and_the_old_row_is_kept_as_history(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, group_id, owner = await _owner_and_group(client, app_schema)
    _, former_player, former = await make_user(app_schema)
    await join(app_schema, group_id, former_player, "admin", left=True)
    token, invite = await _make(client, group_id, owner)

    response = await _join(client, former, token)

    assert response.status_code == 200
    assert response.json()["already_member"] is False
    assert response.json()["playgroup"]["role"] == "member"
    rows = await _memberships(app_schema, group_id, former_player)
    assert [r for r in rows if r[1] is not None], "the former membership row must be kept"
    assert [r[0] for r in rows if r[1] is None] == ["member"]
    assert await _uses(app_schema, invite["id"]) == 1
    assert [g["id"] for g in await _my_groups(client, former)] == [str(group_id)]


@pytest.mark.parametrize("kind", ["revoked", "expired", "used_up", "unknown"])
async def test_joining_with_a_dead_invite_is_410_and_changes_nothing(
    client: httpx.AsyncClient, app_schema: AppSession, kind: str
) -> None:
    owner_id, _, group_id, _ = await _owner_and_group(client, app_schema)
    _, joiner_player, joiner = await make_user(app_schema)
    token = (await _dead_tokens(app_schema, group_id, owner_id))[kind]
    before = await scalar(app_schema, "SELECT coalesce(sum(use_count), 0) FROM invites")

    response = await _join(client, joiner, token)

    assert_envelope(response, 410, "invite_no_longer_valid")
    assert await _memberships(app_schema, group_id, joiner_player) == []
    assert await scalar(app_schema, "SELECT coalesce(sum(use_count), 0) FROM invites") == before


@pytest.mark.parametrize("kind", ["revoked", "expired", "used_up", "unknown"])
async def test_preview_and_register_with_a_dead_invite_are_410(
    client: httpx.AsyncClient, app_schema: AppSession, kind: str
) -> None:
    owner_id, _, group_id, _ = await _owner_and_group(client, app_schema)
    token = (await _dead_tokens(app_schema, group_id, owner_id))[kind]
    users = await count_users(app_schema)

    assert_envelope(await _preview(client, token), 410, "invite_no_longer_valid")
    assert_envelope(await register(client, token), 410, "invite_no_longer_valid")
    assert await count_users(app_schema) == users


async def test_joining_with_a_site_level_invite_is_410_and_no_use_is_counted(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    token = await make_token_invite(app_schema, await make_inviter(app_schema), max_uses=3)
    _, _, joiner = await make_user(app_schema)

    response = await _join(client, joiner, token)

    assert_envelope(response, 410, "invite_no_longer_valid")
    assert await use_count_of(app_schema, token) == 0


async def test_joining_without_credentials_is_401(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, group_id, owner = await _owner_and_group(client, app_schema)
    token, invite = await _make(client, group_id, owner)

    response = await client.post(JOIN, json={"token": token})

    assert response.status_code == 401
    assert await _uses(app_schema, invite["id"]) == 0


async def test_joining_with_a_missing_or_empty_token_is_a_validation_error(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, joiner = await make_user(app_schema)

    for body in ({}, {"token": ""}):
        response = await client.post(JOIN, json=body, headers=joiner)
        assert response.status_code == 422


# ------------------------------------------------------------------------------------------ AC4


async def test_a_plain_member_gets_403_forbidden_on_create_and_no_invite_is_made(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, group_id, _ = await _owner_and_group(client, app_schema)
    _, member_player, member = await make_user(app_schema)
    await join(app_schema, group_id, member_player)

    for body in (None, {"max_uses": 3}):
        assert_envelope(await _create(client, group_id, member, body), 403, "forbidden")

    assert await _invite_count(app_schema) == 0


async def test_a_plain_member_also_cannot_list_or_revoke_a_playgroups_invites(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, group_id, owner = await _owner_and_group(client, app_schema)
    _, member_player, member = await make_user(app_schema)
    await join(app_schema, group_id, member_player)
    _, invite = await _make(client, group_id, owner)

    listing = await client.get(f"{GROUPS}/{group_id}/invites", headers=member)
    revoke = await client.post(f"{GROUPS}/{group_id}/invites/{invite['id']}/revoke", headers=member)

    assert_envelope(listing, 403, "forbidden")
    assert_envelope(revoke, 403, "forbidden")
    assert await scalar(app_schema, "SELECT revoked_at FROM invites") is None


async def test_an_admin_demoted_to_member_can_no_longer_create_with_the_same_token(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, group_id, owner = await _owner_and_group(client, app_schema)
    _, admin_player, admin = await make_user(app_schema)
    await join(app_schema, group_id, admin_player, "admin")
    assert (await _create(client, group_id, admin)).status_code == 201

    demote = await client.patch(
        f"{GROUPS}/{group_id}/members/{admin_player}", json={"role": "member"}, headers=owner
    )

    assert demote.status_code == 200
    assert_envelope(await _create(client, group_id, admin), 403, "forbidden")
    assert await _invite_count(app_schema) == 1


# ------------------------------------------------------------------------------ access rules


async def test_every_kind_of_non_access_gets_the_same_404_on_all_three_invite_routes(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, group_id, owner = await _owner_and_group(client, app_schema)
    _, former_player, former = await make_user(app_schema)
    await join(app_schema, group_id, former_player, "admin", left=True)
    _, outsider = await make_account(app_schema)
    _, site_admin = await make_account(app_schema, admin=True)
    _, invite = await _make(client, group_id, owner)

    def routes(gid: object) -> list[tuple[str, str]]:
        base = f"{GROUPS}/{gid}/invites"
        return [("POST", base), ("GET", base), ("POST", f"{base}/{invite['id']}/revoke")]

    seen: list[dict[str, Any]] = []
    for gid, headers in [
        (group_id, former),
        (group_id, outsider),
        (group_id, site_admin),
        (uuid4(), owner),
        ("not-a-uuid", owner),
    ]:
        for method, url in routes(gid):
            response = await client.request(method, url, headers=headers)
            error = assert_envelope(response, 404, "not_found")
            error.pop("request_id")
            seen.append(error)
    assert all(e == seen[0] for e in seen)
    assert await scalar(app_schema, "SELECT revoked_at FROM invites") is None


async def test_every_invite_route_needs_authentication(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, group_id, owner = await _owner_and_group(client, app_schema)
    _, invite = await _make(client, group_id, owner)
    base = f"{GROUPS}/{group_id}/invites"

    for method, url in [
        ("POST", base),
        ("GET", base),
        ("POST", f"{base}/{invite['id']}/revoke"),
        ("POST", JOIN),
    ]:
        assert (await client.request(method, url)).status_code == 401


async def test_an_owner_of_one_playgroup_cannot_touch_another_playgroups_invites(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, group_a, owner_a = await _owner_and_group(client, app_schema, name="Alpha")
    _, _, group_b, owner_b = await _owner_and_group(client, app_schema, name="Bravo")
    token_b, invite_b = await _make(client, group_b, owner_b)

    assert (await client.get(f"{GROUPS}/{group_b}/invites", headers=owner_a)).status_code == 404
    assert (await _create(client, group_b, owner_a)).status_code == 404
    through_b = f"{GROUPS}/{group_b}/invites/{invite_b['id']}/revoke"
    assert (await client.post(through_b, headers=owner_a)).status_code == 404
    through_a = f"{GROUPS}/{group_a}/invites/{invite_b['id']}/revoke"
    assert_envelope(await client.post(through_a, headers=owner_a), 404, "not_found")

    assert await _invite_count(app_schema) == 1
    assert await scalar(app_schema, "SELECT revoked_at FROM invites") is None
    assert (await _preview(client, token_b)).status_code == 200


# ---------------------------------------------------------------------------------- list


async def test_the_list_shows_only_this_playgroups_invites_with_status_and_uses_never_the_token(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    owner_id, _, group_id, owner = await _owner_and_group(client, app_schema)
    other_group = await _group(client, owner, "Other")
    token, mine = await _make(client, group_id, owner, max_uses=5)
    _, revoked = await _make(client, group_id, owner)
    await client.post(f"{GROUPS}/{group_id}/invites/{revoked['id']}/revoke", headers=owner)
    await _make(client, other_group, owner)
    await make_token_invite(app_schema, owner_id)  # site-level, same creator
    _, _, joiner = await make_user(app_schema)
    await _join(client, joiner, token)

    response = await client.get(f"{GROUPS}/{group_id}/invites", headers=owner)

    items = {i["id"]: i for i in response.json()["items"]}
    assert set(items) == {mine["id"], revoked["id"]}
    assert (items[mine["id"]]["status"], items[mine["id"]]["use_count"]) == ("active", 1)
    assert items[mine["id"]]["max_uses"] == 5
    assert items[revoked["id"]]["status"] == "revoked"
    assert items[revoked["id"]]["revoked_at"] is not None
    assert token not in response.text
    assert response.json()["next_cursor"] is None


async def test_the_list_derives_used_and_expired_status(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    owner_id, _, group_id, owner = await _owner_and_group(client, app_schema)
    await _dead_tokens(app_schema, group_id, owner_id)

    items = (await client.get(f"{GROUPS}/{group_id}/invites", headers=owner)).json()["items"]

    assert sorted(i["status"] for i in items) == ["expired", "revoked", "used"]


async def test_cursor_paging_with_a_small_limit_visits_each_invite_exactly_once(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, group_id, owner = await _owner_and_group(client, app_schema)
    created = [(await _make(client, group_id, owner))[1]["id"] for _ in range(5)]

    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        url = f"{GROUPS}/{group_id}/invites?limit=2" + (f"&cursor={cursor}" if cursor else "")
        body = (await client.get(url, headers=owner)).json()
        assert set(body) == {"items", "next_cursor"}
        assert len(body["items"]) <= 2
        seen += [i["id"] for i in body["items"]]
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert pages == 3
    assert sorted(seen) == sorted(created)


async def test_the_list_page_size_limits_follow_the_convention(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    owner_id, _, group_id, owner = await _owner_and_group(client, app_schema)
    for _ in range(51):
        await make_token_invite(app_schema, owner_id, playgroup_id=group_id)

    default = (await client.get(f"{GROUPS}/{group_id}/invites", headers=owner)).json()
    assert len(default["items"]) == 50
    assert default["next_cursor"] is not None
    maximum = await client.get(f"{GROUPS}/{group_id}/invites?limit=200", headers=owner)
    assert maximum.status_code == 200
    assert len(maximum.json()["items"]) == 51
    for bad in ("201", "0"):
        response = await client.get(f"{GROUPS}/{group_id}/invites?limit={bad}", headers=owner)
        assert response.status_code == 422


async def test_listing_invites_runs_the_same_number_of_statements_for_1_and_8_invites(
    client: httpx.AsyncClient,
    app_schema: AppSession,
    count_statements: Callable[[], AbstractContextManager[StatementCounter]],
) -> None:
    owner_id, _, group_id, owner = await _owner_and_group(client, app_schema)
    url = f"{GROUPS}/{group_id}/invites"
    await make_token_invite(app_schema, owner_id, playgroup_id=group_id)
    with count_statements() as small:
        assert len((await client.get(url, headers=owner)).json()["items"]) == 1
    for _ in range(7):
        await make_token_invite(app_schema, owner_id, playgroup_id=group_id)
    with count_statements() as large:
        assert len((await client.get(url, headers=owner)).json()["items"]) == 8
    assert small.count == large.count, (small.statements, large.statements)


# ---------------------------------------------------------------------------------- revoke


async def test_a_revoked_playgroup_invite_can_no_longer_be_previewed_registered_or_joined(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, group_id, owner = await _owner_and_group(client, app_schema)
    _, joiner_player, joiner = await make_user(app_schema)
    token, invite = await _make(client, group_id, owner, max_uses=5)

    revoke = await client.post(f"{GROUPS}/{group_id}/invites/{invite['id']}/revoke", headers=owner)

    assert revoke.status_code == 200
    assert revoke.json()["status"] == "revoked"
    assert_envelope(await _preview(client, token), 410, "invite_no_longer_valid")
    assert_envelope(await register(client, token), 410, "invite_no_longer_valid")
    assert_envelope(await _join(client, joiner, token), 410, "invite_no_longer_valid")
    assert await _memberships(app_schema, group_id, joiner_player) == []
    assert await _uses(app_schema, invite["id"]) == 0


async def test_revoking_twice_is_harmless(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, group_id, owner = await _owner_and_group(client, app_schema)
    _, invite = await _make(client, group_id, owner)
    url = f"{GROUPS}/{group_id}/invites/{invite['id']}/revoke"

    first = await client.post(url, headers=owner)
    stamp = await scalar(app_schema, "SELECT revoked_at FROM invites")
    second = await client.post(url, headers=owner)

    assert (first.status_code, second.status_code) == (200, 200)
    assert second.json()["status"] == "revoked"
    assert await scalar(app_schema, "SELECT revoked_at FROM invites") == stamp


async def test_an_admin_can_revoke_an_invite_the_owner_made(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, group_id, owner = await _owner_and_group(client, app_schema)
    _, admin_player, admin = await make_user(app_schema)
    await join(app_schema, group_id, admin_player, "admin")
    token, invite = await _make(client, group_id, owner)

    response = await client.post(
        f"{GROUPS}/{group_id}/invites/{invite['id']}/revoke", headers=admin
    )

    assert response.status_code == 200
    assert_envelope(await _preview(client, token), 410, "invite_no_longer_valid")


async def test_revoking_an_unknown_or_malformed_invite_id_is_404(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, group_id, owner = await _owner_and_group(client, app_schema)

    for bad in (uuid4(), "not-a-uuid"):
        response = await client.post(f"{GROUPS}/{group_id}/invites/{bad}/revoke", headers=owner)
        assert_envelope(response, 404, "not_found")


# ------------------------------------------------------------------- Phase 0 not widened


async def test_a_playgroup_owner_who_is_not_a_site_admin_cannot_create_a_site_invite(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, _, owner = await _owner_and_group(client, app_schema)

    assert_envelope(await client.post(INVITES, headers=owner), 403, "forbidden")
    assert await _invite_count(app_schema) == 0


async def test_the_site_invite_list_shows_only_the_callers_site_level_invites(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    admin_id, _, admin = await make_user(app_schema)
    await app_schema.execute(
        text("UPDATE users SET is_site_admin = true WHERE id = :u"), {"u": admin_id}
    )
    await app_schema.commit()
    group_id = await _group(client, admin)
    site = (await client.post(INVITES, headers=admin)).json()
    await _make(client, group_id, admin)
    _, other_admin = await make_account(app_schema, admin=True)
    await client.post(INVITES, headers=other_admin)

    items = (await client.get(INVITES, headers=admin)).json()["items"]

    assert [i["id"] for i in items] == [site["id"]]
    assert items[0]["playgroup_id"] is None


async def test_a_playgroup_invite_cannot_be_revoked_through_the_site_level_route(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, group_id, owner = await _owner_and_group(client, app_schema)
    token, invite = await _make(client, group_id, owner)

    response = await client.post(f"{INVITES}/{invite['id']}/revoke", headers=owner)

    assert_envelope(response, 404, "not_found")
    assert (await _preview(client, token)).status_code == 200
