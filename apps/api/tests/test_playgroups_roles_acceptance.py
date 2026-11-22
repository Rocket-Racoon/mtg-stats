"""Acceptance tests for S-2.4.3 (manage admins, transfer ownership), API part, from the criteria.

AC1 owner promotes/demotes and the next request uses the new permissions, AC2 non-owners get 403,
AC3 transfer swaps owner and admin atomically (exactly one owner), AC4 the owner cannot demote
themselves (409). The 404 rules from S-2.4.1 are re-checked on both new routes. Concurrency is
covered by the implementer's ``test_playgroups_ownership_concurrency.py``.
"""

import uuid
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text

from app.core.db import AppSession
from tests.auth_support import issue
from tests.factories import PlayerFactory, PlaygroupFactory, UserFactory, create
from tests.playgroup_support import join, make_user

pytestmark = pytest.mark.usefixtures("app_schema")
URL = "/api/v1/playgroups"
Headers = dict[str, str]


class Group:
    """An owner, an admin, two plain members and an outsider, all with accounts."""

    id: UUID
    players: dict[str, UUID]
    headers: dict[str, Headers]


async def _group(session: AppSession) -> Group:
    group = Group()
    group.players, group.headers = {}, {}
    row = await create(session, PlaygroupFactory)
    group.id = row.id
    await session.commit()
    for name, role in [
        ("owner", "owner"),
        ("admin", "admin"),
        ("member", "member"),
        ("member2", "member"),
        ("outsider", None),
    ]:
        _, player_id, headers = await make_user(session)
        group.players[name], group.headers[name] = player_id, headers
        if role:
            await join(session, group.id, player_id, role)
    return group


async def _roles(session: AppSession, group_id: UUID) -> dict[UUID, str]:
    """Active memberships straight from the database (raw SQL, so no stale identity map)."""
    rows = await session.execute(
        text("SELECT player_id, role FROM memberships WHERE playgroup_id = :g AND left_at IS NULL"),
        {"g": group_id},
    )
    return {row[0]: row[1] for row in rows}


async def _owners(session: AppSession, group_id: UUID) -> list[UUID]:
    return [p for p, r in (await _roles(session, group_id)).items() if r == "owner"]


def _role_url(group_id: object, player_id: object) -> str:
    return f"{URL}/{group_id}/members/{player_id}"


async def _set_role(
    client: httpx.AsyncClient, group_id: object, player_id: object, role: Any, headers: Headers
) -> httpx.Response:
    return await client.patch(_role_url(group_id, player_id), json={"role": role}, headers=headers)


async def _transfer(
    client: httpx.AsyncClient, group_id: object, player_id: object, headers: Headers
) -> httpx.Response:
    return await client.post(
        f"{URL}/{group_id}/transfer-ownership", json={"player_id": str(player_id)}, headers=headers
    )


async def _my_role(client: httpx.AsyncClient, group_id: UUID, headers: Headers) -> str:
    response = await client.get(URL, params={"limit": 200}, headers=headers)
    assert response.status_code == 200
    (item,) = [i for i in response.json()["items"] if i["id"] == str(group_id)]
    return str(item["role"])


def _shape(response: httpx.Response) -> tuple[int, dict[str, Any]]:
    body = response.json()
    body["error"]["request_id"] = "<request-id>"
    return response.status_code, body


def _code(response: httpx.Response) -> str:
    return str(response.json()["error"]["code"])


# ------------------------------------------------------------------------------------- AC1


async def test_owner_promotes_a_member_to_admin_in_the_response_and_the_database(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    g = await _group(app_schema)
    response = await _set_role(client, g.id, g.players["member"], "admin", g.headers["owner"])
    assert response.status_code == 200
    body = response.json()
    assert body["player_id"] == str(g.players["member"])
    assert body["role"] == "admin"
    assert body["joined_at"]
    assert (await _roles(app_schema, g.id))[g.players["member"]] == "admin"


async def test_a_promoted_member_can_edit_with_the_same_token_where_they_were_403_before(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    g = await _group(app_schema)
    member = g.headers["member"]
    before = await client.patch(f"{URL}/{g.id}", json={"name": "Renamed"}, headers=member)
    assert before.status_code == 403
    assert _code(before) == "forbidden"
    assert await _my_role(client, g.id, member) == "member"

    await _set_role(client, g.id, g.players["member"], "admin", g.headers["owner"])

    after = await client.patch(f"{URL}/{g.id}", json={"name": "Renamed"}, headers=member)
    assert after.status_code == 200
    assert after.json()["name"] == "Renamed"
    assert after.json()["role"] == "admin"
    assert await _my_role(client, g.id, member) == "admin"


async def test_owner_demotes_an_admin_to_member_in_the_response_and_the_database(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    g = await _group(app_schema)
    response = await _set_role(client, g.id, g.players["admin"], "member", g.headers["owner"])
    assert response.status_code == 200
    assert response.json()["role"] == "member"
    assert response.json()["player_id"] == str(g.players["admin"])
    assert (await _roles(app_schema, g.id))[g.players["admin"]] == "member"


async def test_a_demoted_admin_gets_403_on_edit_with_the_same_token_and_lists_member(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    g = await _group(app_schema)
    admin = g.headers["admin"]
    ok = await client.patch(f"{URL}/{g.id}", json={"name": "By admin"}, headers=admin)
    assert ok.status_code == 200
    assert await _my_role(client, g.id, admin) == "admin"

    await _set_role(client, g.id, g.players["admin"], "member", g.headers["owner"])

    after = await client.patch(f"{URL}/{g.id}", json={"name": "Again"}, headers=admin)
    assert after.status_code == 403
    assert _code(after) == "forbidden"
    assert await _my_role(client, g.id, admin) == "member"
    name = (await client.get(f"{URL}/{g.id}", headers=g.headers["owner"])).json()["name"]
    assert name == "By admin"


async def test_a_role_change_leaves_every_other_membership_unchanged(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    g = await _group(app_schema)
    before = await _roles(app_schema, g.id)
    await _set_role(client, g.id, g.players["member"], "admin", g.headers["owner"])
    after = await _roles(app_schema, g.id)
    assert after == {**before, g.players["member"]: "admin"}


# ------------------------------------------------------------------------------------- AC2


@pytest.mark.parametrize("caller", ["admin", "member"])
@pytest.mark.parametrize(
    ("target", "role"),
    [
        ("member2", "admin"),  # promote
        ("admin", "member"),  # demote
        ("owner", "member"),  # demote the owner
        ("member2", "owner"),  # set owner
        ("member2", "member"),  # no-op change
        ("outsider", "admin"),  # non-member target
    ],
)
async def test_a_non_owner_changing_any_role_gets_403_forbidden_and_nothing_changes(
    client: httpx.AsyncClient, app_schema: AppSession, caller: str, target: str, role: str
) -> None:
    g = await _group(app_schema)
    before = await _roles(app_schema, g.id)
    response = await _set_role(client, g.id, g.players[target], role, g.headers[caller])
    assert response.status_code == 403
    assert _code(response) == "forbidden"
    assert await _roles(app_schema, g.id) == before


@pytest.mark.parametrize("caller", ["admin", "member"])
@pytest.mark.parametrize("target", ["owner", "admin", "member", "member2", "outsider", "self"])
async def test_a_non_owner_transferring_ownership_gets_403_forbidden_and_nothing_changes(
    client: httpx.AsyncClient, app_schema: AppSession, caller: str, target: str
) -> None:
    g = await _group(app_schema)
    before = await _roles(app_schema, g.id)
    target_id = g.players[caller if target == "self" else target]
    response = await _transfer(client, g.id, target_id, g.headers[caller])
    assert response.status_code == 403
    assert _code(response) == "forbidden"
    assert await _roles(app_schema, g.id) == before
    assert await _owners(app_schema, g.id) == [g.players["owner"]]


@pytest.mark.parametrize("caller", ["admin", "member"])
async def test_a_non_owner_cannot_tell_whether_a_target_is_in_the_playgroup_on_the_role_route(
    client: httpx.AsyncClient, app_schema: AppSession, caller: str
) -> None:
    g = await _group(app_schema)
    headers = g.headers[caller]
    real = await _set_role(client, g.id, g.players["member2"], "admin", headers)
    outsider = await _set_role(client, g.id, g.players["outsider"], "admin", headers)
    random = await _set_role(client, g.id, uuid.uuid4(), "admin", headers)
    garbage = await _set_role(client, g.id, "not-a-uuid", "admin", headers)
    assert real.status_code == 403
    assert _shape(real) == _shape(outsider) == _shape(random) == _shape(garbage)


@pytest.mark.parametrize("caller", ["admin", "member"])
async def test_a_non_owner_cannot_tell_whether_a_target_is_in_the_playgroup_on_the_transfer_route(
    client: httpx.AsyncClient, app_schema: AppSession, caller: str
) -> None:
    g = await _group(app_schema)
    headers = g.headers[caller]
    real = await _transfer(client, g.id, g.players["member2"], headers)
    outsider = await _transfer(client, g.id, g.players["outsider"], headers)
    random = await _transfer(client, g.id, uuid.uuid4(), headers)
    assert real.status_code == 403
    assert _shape(real) == _shape(outsider) == _shape(random)


# ------------------------------------------------------------------------------------- AC3


async def test_transfer_makes_the_target_owner_and_the_previous_owner_an_admin(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    g = await _group(app_schema)
    before = await _roles(app_schema, g.id)
    response = await _transfer(client, g.id, g.players["member"], g.headers["owner"])
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(g.id)
    assert body["role"] == "admin"  # the caller's new role

    after = await _roles(app_schema, g.id)
    assert after[g.players["member"]] == "owner"
    assert after[g.players["owner"]] == "admin"
    for name in ("admin", "member2"):
        assert after[g.players[name]] == before[g.players[name]]
    assert await _owners(app_schema, g.id) == [g.players["member"]]
    assert await _my_role(client, g.id, g.headers["member"]) == "owner"
    assert await _my_role(client, g.id, g.headers["owner"]) == "admin"


async def test_transfer_to_an_admin_works(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    g = await _group(app_schema)
    response = await _transfer(client, g.id, g.players["admin"], g.headers["owner"])
    assert response.status_code == 200
    roles = await _roles(app_schema, g.id)
    assert roles[g.players["admin"]] == "owner"
    assert roles[g.players["owner"]] == "admin"
    assert await _owners(app_schema, g.id) == [g.players["admin"]]


async def test_the_previous_owner_can_no_longer_change_roles_and_the_new_owner_can(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    g = await _group(app_schema)
    await _transfer(client, g.id, g.players["member"], g.headers["owner"])

    old = await _set_role(client, g.id, g.players["member2"], "admin", g.headers["owner"])
    assert old.status_code == 403
    assert _code(old) == "forbidden"
    old_transfer = await _transfer(client, g.id, g.players["owner"], g.headers["owner"])
    assert old_transfer.status_code == 403
    assert (await _roles(app_schema, g.id))[g.players["member2"]] == "member"

    new = await _set_role(client, g.id, g.players["member2"], "admin", g.headers["member"])
    assert new.status_code == 200
    assert (await _roles(app_schema, g.id))[g.players["member2"]] == "admin"


async def test_the_previous_owner_keeps_admin_edit_rights_with_the_same_token(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    g = await _group(app_schema)
    await _transfer(client, g.id, g.players["member"], g.headers["owner"])
    edit = await client.patch(
        f"{URL}/{g.id}", json={"name": "Old owner"}, headers=g.headers["owner"]
    )
    assert edit.status_code == 200
    assert edit.json()["role"] == "admin"


async def test_a_second_transfer_back_to_the_previous_owner_works(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    g = await _group(app_schema)
    first = await _transfer(client, g.id, g.players["member"], g.headers["owner"])
    assert first.status_code == 200
    back = await _transfer(client, g.id, g.players["owner"], g.headers["member"])
    assert back.status_code == 200
    assert back.json()["role"] == "admin"
    roles = await _roles(app_schema, g.id)
    assert roles[g.players["owner"]] == "owner"
    assert roles[g.players["member"]] == "admin"
    assert await _owners(app_schema, g.id) == [g.players["owner"]]


async def test_a_transfer_does_not_touch_other_playgroups(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    g = await _group(app_schema)
    other = await _group(app_schema)
    await join(app_schema, other.id, g.players["owner"], "member")
    await _transfer(client, g.id, g.players["member"], g.headers["owner"])
    assert (await _roles(app_schema, other.id))[g.players["owner"]] == "member"
    assert await _owners(app_schema, other.id) == [other.players["owner"]]


async def test_a_transfer_to_a_non_member_fails_and_the_original_owner_stays(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    g = await _group(app_schema)
    before = await _roles(app_schema, g.id)
    response = await _transfer(client, g.id, g.players["outsider"], g.headers["owner"])
    assert response.status_code >= 400
    assert await _roles(app_schema, g.id) == before
    assert await _owners(app_schema, g.id) == [g.players["owner"]]


async def test_a_transfer_to_a_former_member_fails_and_the_original_owner_stays(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    g = await _group(app_schema)
    _, former, _ = await make_user(app_schema)
    await join(app_schema, g.id, former, "admin", left=True)
    before = await _roles(app_schema, g.id)
    response = await _transfer(client, g.id, former, g.headers["owner"])
    assert response.status_code >= 400
    assert await _roles(app_schema, g.id) == before
    assert await _owners(app_schema, g.id) == [g.players["owner"]]
    assert await _my_role(client, g.id, g.headers["owner"]) == "owner"


async def test_a_transfer_to_a_nonexistent_player_fails_and_the_original_owner_stays(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    g = await _group(app_schema)
    before = await _roles(app_schema, g.id)
    response = await _transfer(client, g.id, uuid.uuid4(), g.headers["owner"])
    assert response.status_code >= 400
    assert await _roles(app_schema, g.id) == before
    assert await _my_role(client, g.id, g.headers["owner"]) == "owner"


async def test_a_transfer_with_a_malformed_player_id_is_422_and_changes_nothing(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    g = await _group(app_schema)
    before = await _roles(app_schema, g.id)
    response = await client.post(
        f"{URL}/{g.id}/transfer-ownership", json={"player_id": "nope"}, headers=g.headers["owner"]
    )
    assert response.status_code == 422
    assert _code(response) == "validation_error"
    assert await _roles(app_schema, g.id) == before


# ------------------------------------------------------------------------------------- AC4


@pytest.mark.parametrize("role", ["admin", "member"])
async def test_the_owner_demoting_themselves_is_409_transfer_ownership_first(
    client: httpx.AsyncClient, app_schema: AppSession, role: str
) -> None:
    g = await _group(app_schema)
    before = await _roles(app_schema, g.id)
    response = await _set_role(client, g.id, g.players["owner"], role, g.headers["owner"])
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "transfer_ownership_first"
    assert error["message"].rstrip(".!") == "Transfer ownership first"
    assert error["request_id"]
    assert await _roles(app_schema, g.id) == before
    assert await _owners(app_schema, g.id) == [g.players["owner"]]
    assert await _my_role(client, g.id, g.headers["owner"]) == "owner"


# ------------------------------------------------------------------ edge cases (not required
# by the criteria: they pin the implementer's choices so a change is noticed)


async def test_edge_role_endpoint_asked_for_owner_is_409_and_changes_nothing(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    g = await _group(app_schema)
    before = await _roles(app_schema, g.id)
    response = await _set_role(client, g.id, g.players["member"], "owner", g.headers["owner"])
    assert response.status_code == 409
    assert _code(response) == "ownership_transfer_required"
    assert await _roles(app_schema, g.id) == before


async def test_edge_transfer_to_oneself_is_409_already_owner(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    g = await _group(app_schema)
    before = await _roles(app_schema, g.id)
    response = await _transfer(client, g.id, g.players["owner"], g.headers["owner"])
    assert response.status_code == 409
    assert _code(response) == "already_owner"
    assert await _roles(app_schema, g.id) == before


async def test_edge_setting_a_role_to_its_current_value_succeeds_unchanged(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    g = await _group(app_schema)
    before = await _roles(app_schema, g.id)
    response = await _set_role(client, g.id, g.players["admin"], "admin", g.headers["owner"])
    assert response.status_code == 200
    assert response.json()["role"] == "admin"
    assert await _roles(app_schema, g.id) == before


@pytest.mark.parametrize("role", ["superuser", "", None, 5, "Admin"])
async def test_edge_an_invalid_role_value_is_422_validation_error_and_changes_nothing(
    client: httpx.AsyncClient, app_schema: AppSession, role: Any
) -> None:
    g = await _group(app_schema)
    before = await _roles(app_schema, g.id)
    response = await _set_role(client, g.id, g.players["member"], role, g.headers["owner"])
    assert response.status_code == 422
    assert _code(response) == "validation_error"
    assert response.json()["error"]["details"]
    assert await _roles(app_schema, g.id) == before


async def test_edge_a_missing_role_is_422_validation_error(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    g = await _group(app_schema)
    response = await client.patch(
        _role_url(g.id, g.players["member"]), json={}, headers=g.headers["owner"]
    )
    assert response.status_code == 422
    assert _code(response) == "validation_error"


@pytest.mark.parametrize("kind", ["outsider", "random", "malformed", "former"])
async def test_edge_a_target_who_is_not_an_active_member_is_404_not_found_for_the_owner(
    client: httpx.AsyncClient, app_schema: AppSession, kind: str
) -> None:
    g = await _group(app_schema)
    _, former, _ = await make_user(app_schema)
    await join(app_schema, g.id, former, "member", left=True)
    target: object = {
        "outsider": g.players["outsider"],
        "random": uuid.uuid4(),
        "malformed": "not-a-uuid",
        "former": former,
    }[kind]
    before = await _roles(app_schema, g.id)
    response = await _set_role(client, g.id, target, "admin", g.headers["owner"])
    assert response.status_code == 404
    assert _code(response) == "not_found"
    assert await _roles(app_schema, g.id) == before


@pytest.mark.parametrize("kind", ["outsider", "random", "former"])
async def test_edge_a_transfer_target_who_is_not_an_active_member_is_404_not_found(
    client: httpx.AsyncClient, app_schema: AppSession, kind: str
) -> None:
    g = await _group(app_schema)
    _, former, _ = await make_user(app_schema)
    await join(app_schema, g.id, former, "member", left=True)
    target = {"outsider": g.players["outsider"], "random": uuid.uuid4(), "former": former}[kind]
    response = await _transfer(client, g.id, target, g.headers["owner"])
    assert response.status_code == 404
    assert _code(response) == "not_found"
    assert await _owners(app_schema, g.id) == [g.players["owner"]]


# ------------------------------------------------------------------ 404 rules and 401


async def _outsiders(session: AppSession, g: Group) -> dict[str, Headers]:
    _, former, former_headers = await make_user(session)
    await join(session, g.id, former, "admin", left=True)
    site_admin = await create(session, UserFactory, is_site_admin=True)
    await create(session, PlayerFactory, user_id=site_admin.id)
    await session.commit()
    return {
        "non_member": g.headers["outsider"],
        "former_member": former_headers,
        "site_admin": {"Authorization": f"Bearer {issue(site_admin.id)}"},
    }


@pytest.mark.parametrize("who", ["non_member", "former_member", "site_admin"])
@pytest.mark.parametrize("route", ["role", "transfer"])
async def test_a_caller_outside_the_playgroup_gets_the_same_404_as_for_an_unknown_id(
    client: httpx.AsyncClient, app_schema: AppSession, who: str, route: str
) -> None:
    g = await _group(app_schema)
    headers = (await _outsiders(app_schema, g))[who]
    before = await _roles(app_schema, g.id)

    async def call(group_id: object) -> httpx.Response:
        if route == "role":
            return await _set_role(client, group_id, g.players["member"], "admin", headers)
        return await _transfer(client, group_id, g.players["member"], headers)

    real = await call(g.id)
    unknown = await call(uuid.uuid4())
    malformed = await call("not-a-uuid")

    assert real.status_code == 404
    assert _code(real) == "not_found"
    assert _shape(real) == _shape(unknown) == _shape(malformed)
    assert real.json()["error"]["request_id"]
    assert await _roles(app_schema, g.id) == before


async def test_all_outside_callers_are_indistinguishable_from_each_other(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    g = await _group(app_schema)
    callers = await _outsiders(app_schema, g)
    responses = [
        await _set_role(client, g.id, g.players["member"], "admin", h) for h in callers.values()
    ] + [await _transfer(client, g.id, g.players["member"], h) for h in callers.values()]
    first = _shape(responses[0])
    assert first[0] == 404
    assert all(_shape(r) == first for r in responses)


async def test_an_invalid_body_from_an_outside_caller_is_still_404(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    g = await _group(app_schema)
    headers = g.headers["outsider"]
    role = await _set_role(client, g.id, g.players["member"], "superuser", headers)
    transfer = await client.post(
        f"{URL}/{g.id}/transfer-ownership", json={"player_id": "nope"}, headers=headers
    )
    assert role.status_code == 404
    assert transfer.status_code == 404


async def test_both_new_routes_without_a_token_are_401_unauthenticated(
    client: httpx.AsyncClient,
) -> None:
    gid, pid = uuid.uuid4(), uuid.uuid4()
    responses = [
        await client.patch(_role_url(gid, pid), json={"role": "admin"}),
        await client.post(f"{URL}/{gid}/transfer-ownership", json={"player_id": str(pid)}),
    ]
    for response in responses:
        assert response.status_code == 401
        assert _code(response) == "unauthenticated"
