"""Guest players of a playgroup: add, rename, uniqueness and "no credentials" (S-2.3.1)."""

from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.core.db import AppSession
from app.identity import services as identity_services
from app.identity.models import Player, User
from tests.auth_support import issue
from tests.factories import GuestPlayerFactory, PlayerFactory, UserFactory, create
from tests.playgroup_support import join, make_user

pytestmark = pytest.mark.usefixtures("app_schema")
URL = "/api/v1/playgroups"


async def _group(client: httpx.AsyncClient, headers: dict[str, str], name: str = "Friday") -> str:
    return str((await client.post(URL, json={"name": name}, headers=headers)).json()["id"])


async def _add(
    client: httpx.AsyncClient, headers: dict[str, str], group_id: str, name: object
) -> httpx.Response:
    return await client.post(
        f"{URL}/{group_id}/guests", json={"display_name": name}, headers=headers
    )


async def _rename(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    group_id: str,
    guest_id: object,
    name: object,
) -> httpx.Response:
    return await client.patch(
        f"{URL}/{group_id}/guests/{guest_id}", json={"display_name": name}, headers=headers
    )


# --- display_name_key -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("left", "right"),
    [("José", "JOSE"), ("jose", "José"), ("Zoë  Ng", "zoe ng"), ("STRASSE", "straße"), ("é", "é")],
)
def test_display_name_key_ignores_case_accents_and_extra_spaces(left: str, right: str) -> None:
    assert identity_services.display_name_key(left) == identity_services.display_name_key(right)


def test_display_name_key_keeps_different_names_apart() -> None:
    assert identity_services.display_name_key("Ana") != identity_services.display_name_key("Anna")


# --- criterion 1: create ----------------------------------------------------------------------


async def test_a_member_adds_a_guest_with_the_playgroup_as_home(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, owner_player, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    response = await _add(client, owner, group_id, "  Ana  ")
    assert response.status_code == 201
    body = response.json()
    assert body["display_name"] == "Ana"
    assert body["playgroup_id"] == group_id
    assert body["created_by"] == str(owner_player)
    row = await app_schema.get(Player, UUID(body["id"]))
    assert row is not None
    assert (row.is_guest, row.user_id, row.home_playgroup_id) == (True, None, UUID(group_id))
    assert row.created_by_id == owner_player


async def test_a_plain_member_may_add_a_guest(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    _, member_player, member = await make_user(app_schema)
    group_id = await _group(client, owner)
    await join(app_schema, group_id, member_player)
    response = await _add(client, member, group_id, "Bo")
    assert response.status_code == 201
    assert response.json()["created_by"] == str(member_player)


@pytest.mark.parametrize("name", ["a", "x" * 32, "é" * 32])
async def test_names_of_1_to_32_characters_are_accepted(
    client: httpx.AsyncClient, app_schema: AppSession, name: str
) -> None:
    _, _, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    assert (await _add(client, owner, group_id, name)).status_code == 201


@pytest.mark.parametrize("name", ["", "   ", "x" * 33, "a\x00b", "a\nb", 5, None])
async def test_bad_names_are_422(
    client: httpx.AsyncClient, app_schema: AppSession, name: object
) -> None:
    _, _, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    response = await _add(client, owner, group_id, name)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_a_non_member_gets_the_same_404_as_for_an_unknown_playgroup(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    _, _, outsider = await make_user(app_schema)
    group_id = await _group(client, owner)
    denied = await _add(client, outsider, group_id, "Ana")
    unknown = await _add(client, outsider, str(uuid4()), "Ana")
    assert denied.status_code == unknown.status_code == 404
    assert denied.json()["error"]["code"] == "not_found"
    assert denied.json()["error"]["message"] == unknown.json()["error"]["message"]


async def test_a_former_member_cannot_add_a_guest(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    _, former_player, former = await make_user(app_schema)
    group_id = await _group(client, owner)
    await join(app_schema, group_id, former_player, left=True)
    assert (await _add(client, former, group_id, "Ana")).status_code == 404


async def test_adding_a_guest_needs_a_token(client: httpx.AsyncClient) -> None:
    response = await client.post(f"{URL}/{uuid4()}/guests", json={"display_name": "Ana"})
    assert response.status_code == 401


# --- criterion 2: uniqueness ------------------------------------------------------------------


async def test_a_name_matching_a_member_is_409_ignoring_case_and_accents(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    owner_user = await create(app_schema, UserFactory)
    await create(
        app_schema, GuestPlayerFactory
    )  # unrelated guest of no playgroup: must not collide
    await create(app_schema, PlayerFactory, user_id=owner_user.id, display_name="José")
    await app_schema.commit()
    owner = {"Authorization": f"Bearer {issue(owner_user.id)}"}
    group_id = await _group(client, owner)
    response = await _add(client, owner, group_id, "JOSE")
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "player_name_taken"
    assert error["message"] == "A player named 'JOSE' already exists in this playgroup"


async def test_a_name_matching_another_guest_is_409(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    assert (await _add(client, owner, group_id, "Zoë")).status_code == 201
    response = await _add(client, owner, group_id, "ZOE")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "player_name_taken"


async def test_the_same_name_is_fine_in_another_playgroup(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    first = await _group(client, owner, "First")
    second = await _group(client, owner, "Second")
    assert (await _add(client, owner, first, "Ana")).status_code == 201
    assert (await _add(client, owner, second, "Ana")).status_code == 201


async def test_a_former_member_s_name_does_not_block_a_guest(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    _, former_player, _ = await make_user(app_schema)
    group_id = await _group(client, owner)
    await join(app_schema, group_id, former_player, left=True)
    name = (await app_schema.get(Player, former_player)).display_name  # type: ignore[union-attr]
    assert (await _add(client, owner, group_id, name)).status_code == 201


# --- criterion 3: rename ----------------------------------------------------------------------


async def test_the_creator_the_owner_and_an_admin_may_rename(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    _, admin_player, admin = await make_user(app_schema)
    _, creator_player, creator = await make_user(app_schema)
    group_id = await _group(client, owner)
    await join(app_schema, group_id, admin_player, "admin")
    await join(app_schema, group_id, creator_player)
    guest_id = (await _add(client, creator, group_id, "Ana")).json()["id"]

    for headers, name in ((creator, "Ana B"), (owner, "Ana C"), (admin, " Ana D ")):
        response = await _rename(client, headers, group_id, guest_id, name)
        assert response.status_code == 200
        assert response.json()["display_name"] == name.strip()
    row = await app_schema.get(Player, UUID(guest_id), populate_existing=True)
    assert row is not None
    assert row.display_name == "Ana D"


async def test_another_member_gets_403_and_nothing_changes(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    _, other_player, other = await make_user(app_schema)
    group_id = await _group(client, owner)
    await join(app_schema, group_id, other_player)
    guest_id = (await _add(client, owner, group_id, "Ana")).json()["id"]
    response = await _rename(client, other, group_id, guest_id, "Eve")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    row = await app_schema.get(Player, UUID(guest_id), populate_existing=True)
    assert row is not None
    assert row.display_name == "Ana"


async def test_a_non_member_cannot_rename_and_cannot_tell_if_the_guest_exists(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    _, _, outsider = await make_user(app_schema)
    group_id = await _group(client, owner)
    guest_id = (await _add(client, owner, group_id, "Ana")).json()["id"]
    assert (await _rename(client, outsider, group_id, guest_id, "Eve")).status_code == 404


async def test_a_creator_who_left_cannot_rename(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    _, creator_player, creator = await make_user(app_schema)
    group_id = await _group(client, owner)
    membership = await join(app_schema, group_id, creator_player)
    guest_id = (await _add(client, creator, group_id, "Ana")).json()["id"]
    await app_schema.execute(
        text("UPDATE memberships SET left_at = now() WHERE id = :i"), {"i": membership.id}
    )
    await app_schema.commit()
    assert (await _rename(client, creator, group_id, guest_id, "Eve")).status_code == 404


async def test_a_guest_of_another_playgroup_or_a_member_is_404(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, owner_player, owner = await make_user(app_schema)
    _, _, other_owner = await make_user(app_schema)
    mine = await _group(client, owner, "Mine")
    theirs = await _group(client, other_owner, "Theirs")
    foreign_guest = (await _add(client, other_owner, theirs, "Ana")).json()["id"]
    assert (await _rename(client, owner, mine, foreign_guest, "Eve")).status_code == 404
    assert (await _rename(client, owner, mine, owner_player, "Eve")).status_code == 404
    assert (await _rename(client, owner, mine, uuid4(), "Eve")).status_code == 404
    assert (await _rename(client, owner, mine, "not-a-uuid", "Eve")).status_code == 404


async def test_renaming_to_a_taken_name_is_409_but_to_its_own_name_in_another_case_is_fine(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    ana = (await _add(client, owner, group_id, "Ana")).json()["id"]
    await _add(client, owner, group_id, "Bea")
    taken = await _rename(client, owner, group_id, ana, "BÉA")
    assert taken.status_code == 409
    assert taken.json()["error"]["code"] == "player_name_taken"
    assert taken.json()["error"]["message"] == (
        "A player named 'BÉA' already exists in this playgroup"
    )
    assert (await _rename(client, owner, group_id, ana, "ANA")).status_code == 200


async def test_renaming_to_a_member_name_is_409(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, owner_player, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    guest = (await _add(client, owner, group_id, "Ana")).json()["id"]
    member_name = (await app_schema.get(Player, owner_player)).display_name  # type: ignore[union-attr]
    response = await _rename(client, owner, group_id, guest, member_name.upper())
    assert response.status_code == 409


@pytest.mark.parametrize("name", ["", "x" * 33])
async def test_rename_validates_the_length(
    client: httpx.AsyncClient, app_schema: AppSession, name: str
) -> None:
    _, _, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    guest = (await _add(client, owner, group_id, "Ana")).json()["id"]
    assert (await _rename(client, owner, group_id, guest, name)).status_code == 422


async def test_a_merged_guest_cannot_be_renamed(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, owner_player, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    guest = (await _add(client, owner, group_id, "Ana")).json()["id"]
    await app_schema.execute(
        text("UPDATE players SET merged_into_id = :t WHERE id = :g"),
        {"t": owner_player, "g": UUID(guest)},
    )
    await app_schema.commit()
    assert (await _rename(client, owner, group_id, guest, "Eve")).status_code == 404
    # ... and its name no longer blocks a new guest
    assert (await _add(client, owner, group_id, "Ana")).status_code == 201


# --- criterion 4: no credentials --------------------------------------------------------------


async def test_a_guest_has_no_user_and_cannot_sign_in_by_name(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    guest = (await _add(client, owner, group_id, "ana_guest")).json()
    row = await app_schema.get(Player, UUID(guest["id"]))
    assert row is not None
    assert row.user_id is None
    users = (await app_schema.scalars(select(User).where(User.username == "ana_guest"))).all()
    assert users == []
    for password in ("", "password1234", "ana_guest"):
        response = await client.post(
            "/api/v1/auth/login", json={"username": "ana_guest", "password": password}
        )
        assert response.status_code in (401, 422)
        assert "access_token" not in response.text


async def test_the_database_refuses_to_attach_a_user_to_a_guest(app_schema: AppSession) -> None:
    user = await create(app_schema, UserFactory)
    guest = await create(app_schema, GuestPlayerFactory)
    await app_schema.commit()
    with pytest.raises(IntegrityError):
        await app_schema.execute(
            text("UPDATE players SET user_id = :u WHERE id = :g"), {"u": user.id, "g": guest.id}
        )
    await app_schema.rollback()


async def test_no_identity_lookup_resolves_a_guest_to_a_user(app_schema: AppSession) -> None:
    guest = await create(app_schema, GuestPlayerFactory)
    await app_schema.commit()
    assert await identity_services.get_player_for_user(app_schema, guest.id) is None
    with pytest.raises(identity_services.UserNotFoundError):
        await identity_services.set_password(
            app_schema, username=guest.display_name, password="a long enough password"
        )


async def test_a_guest_id_is_not_a_valid_bearer_subject(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    guest = await create(app_schema, GuestPlayerFactory)
    await app_schema.commit()
    response = await client.get(
        URL, headers={"Authorization": f"Bearer {issue(guest.id)}"}
    )  # a token whose subject is a player id, not a user id
    assert response.status_code in (401, 404)  # the id names no user: no player, no data
    assert "items" not in response.json()
