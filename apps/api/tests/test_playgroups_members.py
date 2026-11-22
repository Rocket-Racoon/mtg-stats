"""Role changes and ownership transfer: routes and services (S-2.4.3)."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.db import AppSession
from app.core.errors import NotFoundError, PermissionDeniedError
from app.playgroups import services
from app.playgroups.models import Membership
from tests.playgroup_support import join, make_user

pytestmark = pytest.mark.usefixtures("app_schema")
URL = "/api/v1/playgroups"


async def _group(client: httpx.AsyncClient, headers: dict[str, str]) -> str:
    response = await client.post(URL, json={"name": "Friday Night"}, headers=headers)
    return str(response.json()["id"])


async def _role(client: httpx.AsyncClient, headers: dict[str, str], group_id: str) -> str:
    return str((await client.get(f"{URL}/{group_id}", headers=headers)).json()["role"])


async def _patch(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    group_id: str,
    player_id: UUID | str,
    role: object,
) -> httpx.Response:
    return await client.patch(
        f"{URL}/{group_id}/members/{player_id}", json={"role": role}, headers=headers
    )


async def _transfer(
    client: httpx.AsyncClient, headers: dict[str, str], group_id: str, player_id: UUID | str
) -> httpx.Response:
    return await client.post(
        f"{URL}/{group_id}/transfer-ownership", json={"player_id": str(player_id)}, headers=headers
    )


async def test_owner_promotes_and_demotes_and_the_next_request_sees_it(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    _, member_player, member = await make_user(app_schema)
    group_id = await _group(client, owner)
    await join(app_schema, group_id, member_player)
    rename = {"name": "Renamed"}

    assert (await client.patch(f"{URL}/{group_id}", json=rename, headers=member)).status_code == 403
    response = await _patch(client, owner, group_id, member_player, "admin")
    assert response.status_code == 200
    assert response.json()["role"] == "admin"
    assert response.json()["player_id"] == str(member_player)
    assert await _role(client, member, group_id) == "admin"
    assert (await client.patch(f"{URL}/{group_id}", json=rename, headers=member)).status_code == 200

    response = await _patch(client, owner, group_id, member_player, "member")
    assert response.json()["role"] == "member"
    assert (await client.patch(f"{URL}/{group_id}", json=rename, headers=member)).status_code == 403


async def test_setting_the_current_role_again_succeeds(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    _, member_player, _ = await make_user(app_schema)
    group_id = await _group(client, owner)
    await join(app_schema, group_id, member_player, "admin")
    response = await _patch(client, owner, group_id, member_player, "admin")
    assert response.status_code == 200
    assert response.json()["role"] == "admin"


@pytest.mark.parametrize("caller_role", ["admin", "member"])
async def test_a_non_owner_cannot_change_any_role(
    client: httpx.AsyncClient, app_schema: AppSession, caller_role: str
) -> None:
    _, _, owner = await make_user(app_schema)
    _, caller_player, caller = await make_user(app_schema)
    _, other_player, _ = await make_user(app_schema)
    group_id = await _group(client, owner)
    await join(app_schema, group_id, caller_player, caller_role)
    await join(app_schema, group_id, other_player)

    for target in (other_player, caller_player, uuid4()):
        response = await _patch(client, caller, group_id, target, "admin")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden"
    assert (await _transfer(client, caller, group_id, other_player)).status_code == 403


async def test_outsiders_and_bad_ids_get_the_same_404(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, owner_player, owner = await make_user(app_schema)
    _, _, outsider = await make_user(app_schema)
    _, former_player, former = await make_user(app_schema)
    group_id = await _group(client, owner)
    await join(app_schema, group_id, former_player, left=True)

    for headers in (outsider, former):
        response = await _patch(client, headers, group_id, owner_player, "admin")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
        assert (await _transfer(client, headers, group_id, owner_player)).status_code == 404
    for bad_group in ("nope", str(uuid4())):
        response = await _patch(client, owner, bad_group, owner_player, "admin")
        assert response.status_code == 404
        assert (await _transfer(client, owner, bad_group, owner_player)).status_code == 404


async def test_a_target_who_is_not_an_active_member_is_404(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    _, former_player, _ = await make_user(app_schema)
    _, stranger_player, _ = await make_user(app_schema)
    group_id = await _group(client, owner)
    await join(app_schema, group_id, former_player, left=True)
    for target in (former_player, stranger_player, uuid4(), "not-a-uuid"):
        response = await _patch(client, owner, group_id, target, "admin")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
    for target in (former_player, stranger_player):
        transfer = await _transfer(client, owner, group_id, target)
        assert transfer.status_code == 404
        assert transfer.json()["error"]["code"] == "not_found"


async def test_the_owner_cannot_demote_themselves(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, owner_player, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    for role in ("member", "admin"):
        response = await _patch(client, owner, group_id, owner_player, role)
        assert response.status_code == 409
        error = response.json()["error"]
        assert error["code"] == "transfer_ownership_first"
        assert error["message"] == "Transfer ownership first."
    assert await _role(client, owner, group_id) == "owner"


async def test_the_role_endpoint_never_makes_an_owner(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, owner_player, owner = await make_user(app_schema)
    _, member_player, _ = await make_user(app_schema)
    group_id = await _group(client, owner)
    await join(app_schema, group_id, member_player)
    for target in (member_player, owner_player):
        response = await _patch(client, owner, group_id, target, "owner")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "ownership_transfer_required"


@pytest.mark.parametrize("role", ["superuser", "", None, 3])
async def test_an_invalid_role_is_422(
    client: httpx.AsyncClient, app_schema: AppSession, role: object
) -> None:
    _, _, owner = await make_user(app_schema)
    _, member_player, _ = await make_user(app_schema)
    group_id = await _group(client, owner)
    await join(app_schema, group_id, member_player)
    response = await _patch(client, owner, group_id, member_player, role)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_transfer_makes_the_target_owner_and_the_old_owner_an_admin(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, owner_player, owner = await make_user(app_schema)
    _, member_player, member = await make_user(app_schema)
    group_id = await _group(client, owner)
    await join(app_schema, group_id, member_player)

    response = await _transfer(client, owner, group_id, member_player)
    assert response.status_code == 200
    assert response.json()["role"] == "admin"
    assert await _role(client, owner, group_id) == "admin"
    assert await _role(client, member, group_id) == "owner"
    rows = (await app_schema.scalars(select(Membership).where(Membership.left_at.is_(None)))).all()
    assert {(m.player_id == owner_player, m.role) for m in rows} == {
        (False, "owner"),
        (True, "admin"),
    }
    # The former owner has lost owner-only powers at once.
    denied = await _patch(client, owner, group_id, member_player, "member")
    assert denied.status_code == 403


async def test_transferring_to_an_admin_works_and_to_oneself_is_409(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, owner_player, owner = await make_user(app_schema)
    _, admin_player, admin = await make_user(app_schema)
    group_id = await _group(client, owner)
    await join(app_schema, group_id, admin_player, "admin")

    own = await _transfer(client, owner, group_id, owner_player)
    assert own.status_code == 409
    assert own.json()["error"]["code"] == "already_owner"
    assert await _role(client, owner, group_id) == "owner"

    assert (await _transfer(client, owner, group_id, admin_player)).status_code == 200
    assert await _role(client, admin, group_id) == "owner"


async def test_transfer_body_is_validated(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    for body in ({}, {"player_id": "nope"}, {"player_id": None}):
        response = await client.post(
            f"{URL}/{group_id}/transfer-ownership", json=body, headers=owner
        )
        assert response.status_code == 422


async def test_routes_need_a_token(client: httpx.AsyncClient) -> None:
    gid, pid = uuid4(), uuid4()
    patched = await client.patch(f"{URL}/{gid}/members/{pid}", json={"role": "admin"})
    assert patched.status_code == 401
    posted = await client.post(f"{URL}/{gid}/transfer-ownership", json={"player_id": str(pid)})
    assert posted.status_code == 401


async def test_service_rechecks_the_owner_role_on_the_locked_row(app_schema: AppSession) -> None:
    owner_user, _, _ = await make_user(app_schema)
    _, member_player, _ = await make_user(app_schema)
    group = await services.create_playgroup(app_schema, user_id=owner_user, name="Friday Night")
    await join(app_schema, group.id, member_player)
    access = await services.get_membership(app_schema, playgroup_id=group.id, user_id=owner_user)
    assert access is not None
    assert access.is_owner  # a snapshot taken while still the owner

    await services.transfer_ownership(app_schema, access, player_id=member_player)
    with pytest.raises(PermissionDeniedError):  # the stale snapshot no longer authorises
        await services.change_member_role(
            app_schema, access, player_id=member_player, role="member"
        )
    with pytest.raises(PermissionDeniedError):
        await services.transfer_ownership(app_schema, access, player_id=member_player)


async def test_service_rejects_a_caller_who_left(app_schema: AppSession) -> None:
    owner_user, owner_player, _ = await make_user(app_schema)
    _, member_player, _ = await make_user(app_schema)
    group = await services.create_playgroup(app_schema, user_id=owner_user, name="Friday Night")
    await join(app_schema, group.id, member_player)
    access = await services.get_membership(app_schema, playgroup_id=group.id, user_id=owner_user)
    assert access is not None
    row = (
        await app_schema.scalars(select(Membership).where(Membership.player_id == owner_player))
    ).one()
    row.left_at = datetime.now(UTC)
    await app_schema.flush()
    with pytest.raises(NotFoundError):
        await services.transfer_ownership(app_schema, access, player_id=member_player)


async def test_promoting_before_demoting_would_break_the_single_owner_index(
    app_schema: AppSession,
) -> None:
    """Documents why the transfer demotes first: the partial unique index is not deferrable."""
    owner_user, _, _ = await make_user(app_schema)
    _, member_player, _ = await make_user(app_schema)
    group = await services.create_playgroup(app_schema, user_id=owner_user, name="Friday Night")
    member = await join(app_schema, group.id, member_player)
    member.role = "owner"
    with pytest.raises(IntegrityError):
        async with app_schema.begin_nested():
            await app_schema.flush()
