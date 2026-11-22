"""Playgroup invites: create, list, revoke, preview, register-with and join (S-2.4.2)."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text

from app.core.db import AppSession
from app.core.errors import AuthenticationError
from app.playgroups import services
from app.playgroups.services import hash_invite_token
from tests.invite_support import make_account, make_invite
from tests.playgroup_support import join, make_user
from tests.register_support import (
    PREVIEW,
    assert_envelope,
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


async def _group(client: httpx.AsyncClient, headers: dict[str, str], name: str = "Friday") -> UUID:
    response = await client.post(GROUPS, json={"name": name}, headers=headers)
    return UUID(response.json()["id"])


async def _group_invite(
    session: AppSession, group_id: UUID, created_by: UUID, **fields: Any
) -> str:
    return await make_token_invite(session, created_by, playgroup_id=group_id, **fields)


async def _count(session: AppSession, sql: str, **params: Any) -> int:
    return int(await scalar(session, sql, **params))


async def _memberships(session: AppSession, group_id: UUID, player_id: UUID) -> list[Any]:
    rows = await session.execute(
        text(
            "SELECT role, left_at FROM memberships WHERE playgroup_id = :g AND player_id = :p "
            "ORDER BY joined_at, id"
        ),
        {"g": group_id, "p": player_id},
    )
    return list(rows)


# ------------------------------------------------------------------------------ create (AC1, AC4)


async def test_owner_creates_an_invite_bound_to_the_playgroup(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    user_id, _, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    response = await client.post(f"{GROUPS}/{group_id}/invites", headers=owner)
    assert response.status_code == 201
    body = response.json()
    assert body["playgroup_id"] == str(group_id)
    assert body["status"] == "active"
    assert body["max_uses"] == 1
    assert body["url"].startswith("http")
    assert "/invite/" in body["url"]
    created_by = await scalar(
        app_schema, "SELECT created_by FROM invites WHERE id = :i", i=body["id"]
    )
    assert created_by == user_id


async def test_an_admin_creates_an_invite_with_the_given_limits(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    _, admin_player, admin = await make_user(app_schema)
    group_id = await _group(client, owner)
    await join(app_schema, group_id, admin_player, "admin")
    response = await client.post(
        f"{GROUPS}/{group_id}/invites", json={"expires_in_days": 3, "max_uses": 5}, headers=admin
    )
    assert response.status_code == 201
    assert response.json()["max_uses"] == 5


async def test_a_plain_member_cannot_create_an_invite(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    _, member_player, member = await make_user(app_schema)
    group_id = await _group(client, owner)
    await join(app_schema, group_id, member_player)
    response = await client.post(f"{GROUPS}/{group_id}/invites", headers=member)
    assert_envelope(response, 403, "forbidden")
    assert await _count(app_schema, "SELECT count(*) FROM invites") == 0


async def test_non_members_former_members_and_site_admins_get_404(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    _, former_player, former = await make_user(app_schema)
    _, outsider = await make_account(app_schema)
    _, site_admin = await make_account(app_schema, admin=True)
    group_id = await _group(client, owner)
    await join(app_schema, group_id, former_player, "admin", left=True)
    for headers in (former, outsider, site_admin):
        assert (
            await client.post(f"{GROUPS}/{group_id}/invites", headers=headers)
        ).status_code == 404
        assert (
            await client.get(f"{GROUPS}/{group_id}/invites", headers=headers)
        ).status_code == 404
        revoke = f"{GROUPS}/{group_id}/invites/{uuid4()}/revoke"
        assert (await client.post(revoke, headers=headers)).status_code == 404


async def test_unauthenticated_calls_get_401(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    assert (await client.post(f"{GROUPS}/{group_id}/invites")).status_code == 401
    assert (await client.get(f"{GROUPS}/{group_id}/invites")).status_code == 401
    assert (await client.post(JOIN, json={"token": "x"})).status_code == 401


async def test_the_site_level_invite_route_stays_site_admin_only(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    _, site_admin = await make_account(app_schema, admin=True)
    await _group(client, owner)
    assert (await client.post(INVITES, headers=owner)).status_code == 403
    response = await client.post(INVITES, headers=site_admin)
    assert response.status_code == 201
    assert response.json()["playgroup_id"] is None


# --------------------------------------------------------------------------------- list, revoke


async def test_admins_list_every_invite_of_the_playgroup_and_nothing_else(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    owner_id, _, owner = await make_user(app_schema)
    _, admin_player, admin = await make_user(app_schema)
    _, member_player, member = await make_user(app_schema)
    group_id = await _group(client, owner)
    other_group = await _group(client, owner, "Other")
    await join(app_schema, group_id, admin_player, "admin")
    await join(app_schema, group_id, member_player)
    mine = await client.post(f"{GROUPS}/{group_id}/invites", headers=owner)
    theirs = await client.post(f"{GROUPS}/{group_id}/invites", headers=admin)
    await client.post(f"{GROUPS}/{other_group}/invites", headers=owner)
    await make_invite(app_schema, owner_id)  # a site-level invite by the same user

    page = (await client.get(f"{GROUPS}/{group_id}/invites", headers=admin)).json()
    assert [i["id"] for i in page["items"]] == [theirs.json()["id"], mine.json()["id"]]
    assert all(i["playgroup_id"] == str(group_id) for i in page["items"])
    assert all("url" not in i and "token" not in i for i in page["items"])
    assert (await client.get(f"{GROUPS}/{group_id}/invites", headers=member)).status_code == 403


async def test_the_site_level_list_and_revoke_ignore_playgroup_invites(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    created = (await client.post(f"{GROUPS}/{group_id}/invites", headers=owner)).json()
    assert (await client.get(INVITES, headers=owner)).json()["items"] == []
    revoke = await client.post(f"{INVITES}/{created['id']}/revoke", headers=owner)
    assert revoke.status_code == 404
    status = (await client.get(f"{GROUPS}/{group_id}/invites", headers=owner)).json()["items"][0]
    assert status["status"] == "active"


async def test_playgroup_invite_list_is_paged_with_a_cursor(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    ids = [
        (await client.post(f"{GROUPS}/{group_id}/invites", headers=owner)).json()["id"]
        for _ in range(3)
    ]
    first = (await client.get(f"{GROUPS}/{group_id}/invites?limit=2", headers=owner)).json()
    assert [i["id"] for i in first["items"]] == ids[::-1][:2]
    assert first["next_cursor"] is not None
    second = (
        await client.get(
            f"{GROUPS}/{group_id}/invites?limit=2&cursor={first['next_cursor']}", headers=owner
        )
    ).json()
    assert [i["id"] for i in second["items"]] == ids[::-1][2:]
    assert second["next_cursor"] is None


async def test_listing_runs_the_same_number_of_statements_for_1_and_for_6_invites(
    client: httpx.AsyncClient,
    app_schema: AppSession,
    count_statements: Callable[[], AbstractContextManager[StatementCounter]],
) -> None:
    owner_id, _, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    await make_invite(app_schema, owner_id, playgroup_id=group_id)
    with count_statements() as small:
        assert (
            len((await client.get(f"{GROUPS}/{group_id}/invites", headers=owner)).json()["items"])
            == 1
        )
    for _ in range(5):
        await make_invite(app_schema, owner_id, playgroup_id=group_id)
    with count_statements() as large:
        assert (
            len((await client.get(f"{GROUPS}/{group_id}/invites", headers=owner)).json()["items"])
            == 6
        )
    assert small.count == large.count, (small.statements, large.statements)


async def test_an_admin_revokes_an_invite_another_admin_made_and_it_stops_working(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    owner_id, _, owner = await make_user(app_schema)
    _, admin_player, admin = await make_user(app_schema)
    _, _, newcomer = await make_user(app_schema)
    group_id = await _group(client, owner)
    await join(app_schema, group_id, admin_player, "admin")
    token = new_token()
    invite_id = await make_invite(
        app_schema, owner_id, token_hash=hash_invite_token(token), playgroup_id=group_id
    )
    revoke = f"{GROUPS}/{group_id}/invites/{invite_id}/revoke"
    first = await client.post(revoke, headers=admin)
    assert first.status_code == 200
    assert first.json()["status"] == "revoked"
    assert (await client.post(revoke, headers=admin)).status_code == 200  # idempotent
    response = await client.post(JOIN, json={"token": token}, headers=newcomer)
    assert_envelope(response, 410, "invite_no_longer_valid")


async def test_revoking_an_invite_of_another_playgroup_is_a_404(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    _, _, other_owner = await make_user(app_schema)
    mine = await _group(client, owner)
    theirs = await _group(client, other_owner, "Theirs")
    created = (await client.post(f"{GROUPS}/{theirs}/invites", headers=other_owner)).json()
    response = await client.post(f"{GROUPS}/{mine}/invites/{created['id']}/revoke", headers=owner)
    assert response.status_code == 404
    bad = await client.post(f"{GROUPS}/{mine}/invites/not-a-uuid/revoke", headers=owner)
    assert bad.status_code == 404


# ------------------------------------------------------------------------------------ preview


async def test_the_preview_shows_the_playgroup_name_and_nothing_more_about_it(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    owner_id, _, owner = await make_user(app_schema)
    response = await client.post(
        GROUPS, json={"name": "Friday Night", "description": "secret words"}, headers=owner
    )
    group_id = UUID(response.json()["id"])
    token = await _group_invite(app_schema, group_id, owner_id)
    preview = await client.post(PREVIEW, json={"token": token})
    assert preview.status_code == 200
    assert set(preview.json()) == {"inviter_display_name", "expires_at", "playgroup_name"}
    assert preview.json()["playgroup_name"] == "Friday Night"
    assert "secret words" not in preview.text
    assert str(group_id) not in preview.text


async def test_the_preview_of_a_site_level_invite_has_no_playgroup_name(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    token = await make_token_invite(app_schema, await make_inviter(app_schema))
    preview = await client.post(PREVIEW, json={"token": token})
    assert preview.json()["playgroup_name"] is None


@pytest.mark.parametrize("state", ["revoked", "expired", "used", "unknown"])
async def test_the_preview_of_a_dead_playgroup_invite_is_410(
    client: httpx.AsyncClient, app_schema: AppSession, state: str
) -> None:
    owner_id, _, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    token = new_token()
    if state != "unknown":
        token = await _group_invite(app_schema, group_id, owner_id, **_dead(state))
    response = await client.post(PREVIEW, json={"token": token})
    assert_envelope(response, 410, "invite_no_longer_valid")


def _dead(state: str) -> dict[str, Any]:
    return {
        "revoked": {"revoked_at": datetime.now(UTC)},
        "expired": {"expires_at": datetime.now(UTC) - timedelta(seconds=5)},
        "used": {"use_count": 1, "max_uses": 1},
    }[state]


# -------------------------------------------------------------------------- register (AC2)


async def test_registering_with_a_playgroup_invite_makes_the_new_user_a_member(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    owner_id, _, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    token = await _group_invite(app_schema, group_id, owner_id)
    response = await register(client, token)
    assert response.status_code == 201
    rows = await _memberships_of_username(app_schema, group_id, "newbie")
    assert [r.role for r in rows] == ["member"]
    assert rows[0].left_at is None
    assert await use_count_of(app_schema, token) == 1


async def _memberships_of_username(session: AppSession, group_id: UUID, username: str) -> list[Any]:
    result = await session.execute(
        text(
            "SELECT m.role, m.left_at FROM memberships m JOIN players p ON p.id = m.player_id "
            "JOIN users u ON u.id = p.user_id WHERE m.playgroup_id = :g AND lower(u.username) = :u"
        ),
        {"g": group_id, "u": username},
    )
    return list(result)


async def test_a_failed_registration_leaves_no_membership_and_no_use(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    owner_id, _, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    token = await _group_invite(app_schema, group_id, owner_id, max_uses=2)
    assert (await register(client, token)).status_code == 201
    memberships = await _count(app_schema, "SELECT count(*) FROM memberships")
    taken = await register(client, token)  # same username again
    assert_envelope(taken, 409, "username_taken")
    assert await _count(app_schema, "SELECT count(*) FROM memberships") == memberships
    assert await use_count_of(app_schema, token) == 1


async def test_registering_with_a_site_level_invite_joins_no_playgroup(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    token = await make_token_invite(app_schema, await make_inviter(app_schema))
    assert (await register(client, token)).status_code == 201
    assert await _count(app_schema, "SELECT count(*) FROM memberships") == 0
    assert await use_count_of(app_schema, token) == 1


async def test_a_revoked_playgroup_invite_cannot_register(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    owner_id, _, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    token = await _group_invite(app_schema, group_id, owner_id, revoked_at=datetime.now(UTC))
    assert_envelope(await register(client, token), 410, "invite_no_longer_valid")
    assert await _count(app_schema, "SELECT count(*) FROM users WHERE username = 'newbie'") == 0


# ------------------------------------------------------------------------------- join (AC3)


async def test_a_signed_in_user_joins_as_member_and_the_use_count_goes_up(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    owner_id, _, owner = await make_user(app_schema)
    _, player_id, newcomer = await make_user(app_schema)
    group_id = await _group(client, owner, "Friday Night")
    token = await _group_invite(app_schema, group_id, owner_id, max_uses=3)
    response = await client.post(JOIN, json={"token": token}, headers=newcomer)
    assert response.status_code == 200
    body = response.json()
    assert body["already_member"] is False
    assert body["playgroup"]["id"] == str(group_id)
    assert body["playgroup"]["name"] == "Friday Night"
    assert body["playgroup"]["role"] == "member"
    assert await use_count_of(app_schema, token) == 1
    rows = await _memberships(app_schema, group_id, player_id)
    assert [(r.role, r.left_at) for r in rows] == [("member", None)]
    listed = (await client.get(GROUPS, headers=newcomer)).json()["items"]
    assert [g["id"] for g in listed] == [str(group_id)]


@pytest.mark.parametrize("role", ["member", "admin", "owner"])
async def test_an_existing_member_is_told_so_and_no_use_is_counted(
    client: httpx.AsyncClient, app_schema: AppSession, role: str
) -> None:
    owner_id, _, owner = await make_user(app_schema)
    _, player_id, other = await make_user(app_schema)
    group_id = await _group(client, owner, "Friday Night")
    await join(app_schema, group_id, player_id, "member" if role == "owner" else role)
    token = await _group_invite(app_schema, group_id, owner_id, max_uses=3)
    caller = owner if role == "owner" else other
    response = await client.post(JOIN, json={"token": token}, headers=caller)
    assert response.status_code == 200
    assert response.json()["already_member"] is True
    assert response.json()["playgroup"]["name"] == "Friday Night"
    assert response.json()["playgroup"]["role"] == role
    assert await use_count_of(app_schema, token) == 0
    assert await _count(app_schema, "SELECT count(*) FROM memberships") == 2


async def test_a_former_member_joins_again_with_a_new_row_and_keeps_the_history(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    owner_id, _, owner = await make_user(app_schema)
    _, player_id, former = await make_user(app_schema)
    group_id = await _group(client, owner)
    await join(app_schema, group_id, player_id, "admin", left=True)
    token = await _group_invite(app_schema, group_id, owner_id)
    response = await client.post(JOIN, json={"token": token}, headers=former)
    assert response.status_code == 200
    assert response.json()["already_member"] is False
    assert response.json()["playgroup"]["role"] == "member"
    rows = await _memberships(app_schema, group_id, player_id)
    assert [(r.role, r.left_at is None) for r in rows] == [("admin", False), ("member", True)]


@pytest.mark.parametrize("state", ["revoked", "expired", "used"])
async def test_a_dead_invite_cannot_be_joined(
    client: httpx.AsyncClient, app_schema: AppSession, state: str
) -> None:
    owner_id, _, owner = await make_user(app_schema)
    _, player_id, newcomer = await make_user(app_schema)
    group_id = await _group(client, owner)
    token = await _group_invite(app_schema, group_id, owner_id, **_dead(state))
    response = await client.post(JOIN, json={"token": token}, headers=newcomer)
    assert_envelope(response, 410, "invite_no_longer_valid")
    assert await _memberships(app_schema, group_id, player_id) == []


async def test_an_unknown_token_and_a_site_level_invite_cannot_be_joined(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, newcomer = await make_user(app_schema)
    site_token = await make_token_invite(app_schema, await make_inviter(app_schema))
    for token in (new_token(), site_token):
        response = await client.post(JOIN, json={"token": token}, headers=newcomer)
        assert_envelope(response, 410, "invite_no_longer_valid")
    assert await use_count_of(app_schema, site_token) == 0


async def test_a_single_use_invite_admits_one_user_and_the_next_gets_410(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    owner_id, _, owner = await make_user(app_schema)
    _, _, first = await make_user(app_schema)
    _, second_player, second = await make_user(app_schema)
    group_id = await _group(client, owner)
    token = await _group_invite(app_schema, group_id, owner_id)
    assert (await client.post(JOIN, json={"token": token}, headers=first)).status_code == 200
    again = await client.post(JOIN, json={"token": token}, headers=second)
    assert_envelope(again, 410, "invite_no_longer_valid")
    assert await _memberships(app_schema, group_id, second_player) == []
    assert await use_count_of(app_schema, token) == 1


async def test_a_deactivated_account_cannot_join(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    owner_id, _, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    _, headers = await make_account(app_schema, active=False)
    token = await _group_invite(app_schema, group_id, owner_id)
    response = await client.post(JOIN, json={"token": token}, headers=headers)
    assert_envelope(response, 401, "unauthenticated")
    assert await use_count_of(app_schema, token) == 0


async def test_the_service_rejects_a_deactivated_user(app_schema: AppSession) -> None:
    user_id, _ = await make_account(app_schema, active=False)
    with pytest.raises(AuthenticationError):
        await services.join_with_invite(app_schema, user_id=user_id, token=new_token())


async def test_the_invite_body_validates_the_token(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, newcomer = await make_user(app_schema)
    for body in ({}, {"token": ""}, {"token": "x" * 129}):
        response = await client.post(JOIN, json=body, headers=newcomer)
        assert response.status_code == 422


# ------------------------------------------------- join order of checks: members vs dead invites


@pytest.mark.parametrize("state", ["used", "expired", "revoked"])
@pytest.mark.parametrize("role", ["member", "admin", "owner"])
async def test_a_member_reopening_a_dead_invite_is_told_they_are_already_in(
    client: httpx.AsyncClient, app_schema: AppSession, state: str, role: str
) -> None:
    owner_id, _, owner = await make_user(app_schema)
    _, player_id, other = await make_user(app_schema)
    group_id = await _group(client, owner, "Friday Night")
    await join(app_schema, group_id, player_id, "member" if role == "owner" else role)
    token = await _group_invite(app_schema, group_id, owner_id, **_dead(state))
    uses = await use_count_of(app_schema, token)
    memberships = await _count(app_schema, "SELECT count(*) FROM memberships")
    caller = owner if role == "owner" else other
    response = await client.post(JOIN, json={"token": token}, headers=caller)
    assert response.status_code == 200
    assert response.json()["already_member"] is True
    assert response.json()["playgroup"]["name"] == "Friday Night"
    assert await use_count_of(app_schema, token) == uses
    assert await _count(app_schema, "SELECT count(*) FROM memberships") == memberships


async def test_joining_with_a_single_use_link_then_reopening_it_says_already_in(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    owner_id, _, owner = await make_user(app_schema)
    _, _, newcomer = await make_user(app_schema)
    group_id = await _group(client, owner)
    token = await _group_invite(app_schema, group_id, owner_id)
    assert (await client.post(JOIN, json={"token": token}, headers=newcomer)).status_code == 200
    again = await client.post(JOIN, json={"token": token}, headers=newcomer)
    assert again.status_code == 200
    assert again.json()["already_member"] is True
    assert await use_count_of(app_schema, token) == 1


@pytest.mark.parametrize("state", ["used", "expired", "revoked"])
async def test_a_former_member_with_a_dead_invite_gets_410(
    client: httpx.AsyncClient, app_schema: AppSession, state: str
) -> None:
    owner_id, _, owner = await make_user(app_schema)
    _, player_id, former = await make_user(app_schema)
    group_id = await _group(client, owner)
    await join(app_schema, group_id, player_id, "member", left=True)
    token = await _group_invite(app_schema, group_id, owner_id, **_dead(state))
    response = await client.post(JOIN, json={"token": token}, headers=former)
    assert_envelope(response, 410, "invite_no_longer_valid")


@pytest.mark.parametrize("state", ["used", "expired", "revoked", "unknown"])
async def test_a_non_member_gets_the_same_410_for_every_dead_state(
    client: httpx.AsyncClient, app_schema: AppSession, state: str
) -> None:
    owner_id, _, owner = await make_user(app_schema)
    _, _, outsider = await make_user(app_schema)
    group_id = await _group(client, owner)
    token = new_token()
    if state != "unknown":
        token = await _group_invite(app_schema, group_id, owner_id, **_dead(state))
    response = await client.post(JOIN, json={"token": token}, headers=outsider)
    error = assert_envelope(response, 410, "invite_no_longer_valid")
    assert error["message"] == "Invite no longer valid."
    assert error["details"] is None


@pytest.mark.parametrize("state", ["used", "expired", "revoked"])
async def test_a_member_of_another_playgroup_gets_410_for_a_dead_invite(
    client: httpx.AsyncClient, app_schema: AppSession, state: str
) -> None:
    owner_id, _, owner = await make_user(app_schema)
    _, other_owner_player, other_owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    await _group(client, other_owner, "Elsewhere")
    token = await _group_invite(app_schema, group_id, owner_id, **_dead(state))
    response = await client.post(JOIN, json={"token": token}, headers=other_owner)
    assert_envelope(response, 410, "invite_no_longer_valid")
    assert await _memberships(app_schema, group_id, other_owner_player) == []


async def test_a_deactivated_member_gets_401_before_the_already_member_answer(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    owner_id, _, owner = await make_user(app_schema)
    user_id, player_id, headers = await make_user(app_schema)
    group_id = await _group(client, owner)
    await join(app_schema, group_id, player_id)
    token = await _group_invite(app_schema, group_id, owner_id)
    await app_schema.execute(
        text("UPDATE users SET is_active = false WHERE id = :u"), {"u": user_id}
    )
    await app_schema.commit()
    response = await client.post(JOIN, json={"token": token}, headers=headers)
    assert_envelope(response, 401, "unauthenticated")
