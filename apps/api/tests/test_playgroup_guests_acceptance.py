"""Acceptance tests for S-2.3.1 "Add a guest player to a playgroup" (API part).

Written from the four acceptance criteria. ``test_playgroup_guests.py`` holds the implementer's
happy-path and basic checks; these add boundaries, the stored row, who may and may not act,
non-disclosure, Unicode uniqueness, rename edge cases and every real credential path.
"""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.core.db import AppSession
from app.identity.models import Player, User
from tests.auth_support import issue
from tests.factories import GuestPlayerFactory, PlayerFactory, UserFactory, create
from tests.playgroup_support import join, make_user
from tests.rate_limit_support import client_from
from tests.register_support import (
    assert_envelope,
    count_players,
    count_users,
    make_inviter,
    make_token_invite,
    register,
)

pytestmark = pytest.mark.usefixtures("app_schema")
URL = "/api/v1/playgroups"
LOGIN = "/api/v1/auth/login"
GUEST_KEYS = {"id", "playgroup_id", "display_name", "avatar_color", "created_by", "created_at"}
TAKEN = "A player named '{}' already exists in this playgroup"


async def _group(client: httpx.AsyncClient, headers: dict[str, str], name: str = "Friday") -> str:
    return str((await client.post(URL, json={"name": name}, headers=headers)).json()["id"])


async def _add(
    client: httpx.AsyncClient, headers: dict[str, str], group_id: object, name: object
) -> httpx.Response:
    return await client.post(
        f"{URL}/{group_id}/guests", json={"display_name": name}, headers=headers
    )


async def _rename(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    group_id: object,
    guest_id: object,
    name: object,
) -> httpx.Response:
    return await client.patch(
        f"{URL}/{group_id}/guests/{guest_id}", json={"display_name": name}, headers=headers
    )


async def _guest_count(session: AppSession) -> int:
    result = await session.execute(text("SELECT count(*) FROM players WHERE is_guest"))
    return int(result.scalar_one())


async def _name_of(session: AppSession, player_id: object) -> str:
    row = await session.get(Player, UUID(str(player_id)), populate_existing=True)
    assert row is not None
    return row.display_name


async def _set_role(session: AppSession, player_id: UUID, role: str) -> None:
    await session.execute(
        text("UPDATE memberships SET role = :r WHERE player_id = :p"), {"r": role, "p": player_id}
    )
    await session.commit()


async def _member_named(
    session: AppSession, group_id: str, name: str, *, left: bool = False
) -> UUID:
    """Create a user whose player has this display name and join it to the playgroup."""
    user = await create(session, UserFactory)
    player = await create(session, PlayerFactory, user_id=user.id, display_name=name)
    player_id = player.id
    await session.commit()
    await join(session, group_id, player_id, left=left)
    return player_id


async def _group_with_member_name(
    client: httpx.AsyncClient, session: AppSession, member_name: str
) -> tuple[dict[str, str], str]:
    """A playgroup whose owner has the given display name; returns the owner's headers and id."""
    user = await create(session, UserFactory)
    await create(session, PlayerFactory, user_id=user.id, display_name=member_name)
    await session.commit()
    headers = {"Authorization": f"Bearer {issue(user.id)}"}
    return headers, await _group(client, headers)


# --- criterion 1: create ----------------------------------------------------------------------


async def test_the_201_body_has_exactly_the_documented_fields(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, owner_player, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    response = await _add(client, owner, group_id, "Ana")
    assert response.status_code == 201
    body: dict[str, Any] = response.json()
    assert set(body) == GUEST_KEYS
    assert body["playgroup_id"] == group_id
    assert body["created_by"] == str(owner_player)
    assert UUID(body["id"]).version == 7
    assert datetime.fromisoformat(body["created_at"]).tzinfo is not None
    row = await app_schema.get(Player, UUID(body["id"]))
    assert row is not None
    assert body["avatar_color"] == row.avatar_color


@pytest.mark.parametrize(
    "name",
    ["😀" * 32, "é" * 16, "é" * 32, "日本語" * 10 + "ab", "a b" * 10 + "cd"],
    ids=["32-astral", "32-combining-codepoints", "32-accented", "32-cjk", "32-with-spaces"],
)
async def test_32_characters_are_accepted_whatever_their_byte_length(
    client: httpx.AsyncClient, app_schema: AppSession, name: str
) -> None:
    assert len(name) == 32
    _, _, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    response = await _add(client, owner, group_id, name)
    assert response.status_code == 201
    assert response.json()["display_name"] == name


@pytest.mark.parametrize(
    "name",
    ["😀" * 33, "é" * 33, "x" * 33, "x" * 200, "\t \n", " ", ""],  # noqa: RUF001
    ids=[
        "33-astral",
        "33-accented",
        "33-ascii",
        "200-ascii",
        "whitespace-only",
        "nbsp-only",
        "empty",
    ],
)
async def test_names_outside_1_to_32_characters_are_422_and_create_nothing(
    client: httpx.AsyncClient, app_schema: AppSession, name: str
) -> None:
    _, _, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    response = await _add(client, owner, group_id, name)
    assert_envelope(response, 422, "validation_error")
    assert await _guest_count(app_schema) == 0


async def test_a_33_character_name_that_trims_to_32_is_accepted(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    response = await _add(client, owner, group_id, " " + "x" * 32)
    assert response.status_code == 201
    assert response.json()["display_name"] == "x" * 32


@pytest.mark.parametrize("body", [{}, {"name": "Ana"}, {"display_name": ["Ana"]}, []])
async def test_a_malformed_body_is_422(
    client: httpx.AsyncClient, app_schema: AppSession, body: object
) -> None:
    _, _, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    response = await client.post(f"{URL}/{group_id}/guests", json=body, headers=owner)
    assert_envelope(response, 422, "validation_error")
    assert await _guest_count(app_schema) == 0


async def test_the_stored_guest_has_no_user_a_home_playgroup_its_creator_and_no_membership(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    _, member_player, member = await make_user(app_schema)
    group_id = await _group(client, owner)
    await join(app_schema, group_id, member_player)
    guest_id = (await _add(client, member, group_id, "Ana")).json()["id"]
    row = await app_schema.get(Player, UUID(guest_id))
    assert row is not None
    assert row.is_guest is True
    assert row.user_id is None
    assert row.home_playgroup_id == UUID(group_id)
    assert row.created_by_id == member_player
    assert row.merged_into_id is None
    memberships = await app_schema.execute(
        text("SELECT count(*) FROM memberships WHERE player_id = :p"), {"p": UUID(guest_id)}
    )
    assert memberships.scalar_one() == 0


@pytest.mark.parametrize("role", ["owner", "admin", "member"])
async def test_every_active_role_may_add_a_guest(
    client: httpx.AsyncClient, app_schema: AppSession, role: str
) -> None:
    _, owner_player, owner = await make_user(app_schema)
    _, other_player, other = await make_user(app_schema)
    group_id = await _group(client, owner)
    await join(app_schema, group_id, other_player, "admin" if role == "admin" else "member")
    caller, caller_player = (owner, owner_player) if role == "owner" else (other, other_player)
    response = await _add(client, caller, group_id, f"Guest of {role}")
    assert response.status_code == 201
    assert response.json()["created_by"] == str(caller_player)


async def test_a_non_member_a_former_member_and_another_group_s_owner_create_nothing(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    _, _, outsider = await make_user(app_schema)
    _, former_player, former = await make_user(app_schema)
    _, _, rival_owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    await _group(client, rival_owner, "Elsewhere")
    await join(app_schema, group_id, former_player, left=True)
    for headers in (outsider, former, rival_owner):
        response = await _add(client, headers, group_id, "Ana")
        assert_envelope(response, 404, "not_found")
    assert await _guest_count(app_schema) == 0


async def test_an_unauthenticated_add_and_rename_are_401_and_change_nothing(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    guest_id = (await _add(client, owner, group_id, "Ana")).json()["id"]
    added = await client.post(f"{URL}/{group_id}/guests", json={"display_name": "Bo"})
    renamed = await client.patch(
        f"{URL}/{group_id}/guests/{guest_id}", json={"display_name": "Eve"}
    )
    assert_envelope(added, 401, "unauthenticated")
    assert_envelope(renamed, 401, "unauthenticated")
    assert await _guest_count(app_schema) == 1
    assert await _name_of(app_schema, guest_id) == "Ana"


async def test_the_404_for_a_playgroup_you_are_not_in_matches_an_unknown_playgroup_exactly(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    _, _, outsider = await make_user(app_schema)
    group_id = await _group(client, owner)
    guest_id = (await _add(client, owner, group_id, "Ana")).json()["id"]

    def _scrub(response: httpx.Response) -> dict[str, Any]:
        error = dict(response.json()["error"])
        error.pop("request_id")
        return {"status": response.status_code, **error}

    real_add = _scrub(await _add(client, outsider, group_id, "Zed"))
    fake_add = _scrub(await _add(client, outsider, uuid4(), "Zed"))
    real_patch = _scrub(await _rename(client, outsider, group_id, guest_id, "Zed"))
    fake_patch = _scrub(await _rename(client, outsider, uuid4(), guest_id, "Zed"))
    assert real_add == fake_add
    assert real_patch == fake_patch
    assert real_add["status"] == 404
    assert real_patch["status"] == 404


async def test_an_outsider_learns_nothing_about_names_through_a_409(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    _, _, outsider = await make_user(app_schema)
    group_id = await _group(client, owner)
    guest_id = (await _add(client, owner, group_id, "Ana")).json()["id"]
    # "Ana" is taken, but an outsider must see 404, never 409.
    assert (await _add(client, outsider, group_id, "ANA")).status_code == 404
    assert (await _rename(client, outsider, group_id, guest_id, "ANA")).status_code == 404
    assert await _guest_count(app_schema) == 1


async def test_playgroup_and_creator_in_the_body_are_never_honoured(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, owner_player, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    response = await client.post(
        f"{URL}/{group_id}/guests",
        json={"display_name": "Ana", "playgroup_id": str(uuid4()), "created_by": str(uuid4())},
        headers=owner,
    )
    # An extra field is either ignored or rejected, never honoured.
    if response.status_code == 201:
        body = response.json()
        assert body["playgroup_id"] == group_id
        assert body["created_by"] == str(owner_player)
    else:
        assert response.status_code == 422
        assert await _guest_count(app_schema) == 0


# --- criterion 2: uniqueness ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("existing", "submitted"),
    [
        ("José", "jose"),
        ("jose", "JOSÉ"),
        ("ÅSA", "asa"),
        ("asa", "ÅSA"),
        ("ß", "SS"),
        ("Strasse", "STRAßE"),
        ("ＡＮＡ", "ana"),  # noqa: RUF001
        ("Fido", "ﬁdo"),
        ("Zoe", "Zoë"),
        ("Zoë", "Zoë"),
        ("Zoë", "ZOË"),
        ("Ana Bo", "Ana  Bo"),
        ("Ana  Bo", "ana bo"),
        ("Ana Bo", "Ana Bo"),  # noqa: RUF001
        ("Ana", "  Ana"),
        ("Ana", "ana  "),
    ],
)
async def test_a_member_s_name_blocks_a_guest_that_differs_only_in_case_accents_or_spacing(
    client: httpx.AsyncClient, app_schema: AppSession, existing: str, submitted: str
) -> None:
    owner, group_id = await _group_with_member_name(client, app_schema, existing)
    response = await _add(client, owner, group_id, submitted)
    error = assert_envelope(response, 409, "player_name_taken")
    assert error["message"] == TAKEN.format(submitted.strip())
    assert await _guest_count(app_schema) == 0


@pytest.mark.parametrize(
    ("existing", "submitted"),
    [("José", "JOSE"), ("ÅSA", "asa"), ("ß", "SS"), ("Zoë", "Zoë"), ("Ana Bo", " ana  bo ")],
)
async def test_a_guest_s_name_blocks_a_guest_that_differs_only_in_case_accents_or_spacing(
    client: httpx.AsyncClient, app_schema: AppSession, existing: str, submitted: str
) -> None:
    _, _, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    assert (await _add(client, owner, group_id, existing)).status_code == 201
    response = await _add(client, owner, group_id, submitted)
    error = assert_envelope(response, 409, "player_name_taken")
    assert error["message"] == TAKEN.format(submitted.strip())
    assert await _guest_count(app_schema) == 1


async def test_the_409_names_the_submitted_text_in_its_message_and_details(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    await _add(client, owner, group_id, "José")
    error = assert_envelope(await _add(client, owner, group_id, "jOsE"), 409, "player_name_taken")
    # X is the name the caller typed (not the existing player's spelling).
    assert error["message"] == "A player named 'jOsE' already exists in this playgroup"
    assert error["details"] == {"display_name": "jOsE"}


async def test_a_guest_can_share_a_name_with_a_member_or_guest_of_another_playgroup(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    owner_a, group_a = await _group_with_member_name(client, app_schema, "Zoë")
    _, _, owner_b = await make_user(app_schema)
    group_b = await _group(client, owner_b)
    assert (await _add(client, owner_a, group_a, "Guest A")).status_code == 201
    assert (await _add(client, owner_b, group_b, "ZOE")).status_code == 201
    assert (await _add(client, owner_b, group_b, "guest a")).status_code == 201


async def test_names_that_differ_beyond_case_and_accents_are_allowed(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    owner, group_id = await _group_with_member_name(client, app_schema, "Ana")
    for name in ("Anna", "Ana-B", "Ana B", "AnaB", "Ána2", "An a", "Ana."):
        response = await _add(client, owner, group_id, name)
        assert response.status_code == 201, name


async def test_a_former_member_s_name_does_not_block_a_guest_because_they_are_not_a_member(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    # Criterion 2 says "an existing member or guest": a player who left is neither.
    _, _, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    await _member_named(app_schema, group_id, "Xavi", left=True)
    assert (await _add(client, owner, group_id, "XAVI")).status_code == 201


async def test_a_merged_guest_s_name_does_not_block_a_new_guest(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, owner_player, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    await create(
        app_schema,
        GuestPlayerFactory,
        display_name="Mia",
        home_playgroup_id=UUID(group_id),
        merged_into_id=owner_player,
    )
    await app_schema.commit()
    assert (await _add(client, owner, group_id, "MIA")).status_code == 201


async def test_a_guest_of_another_playgroup_with_the_same_name_does_not_block(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    await create(app_schema, GuestPlayerFactory, display_name="Nia", home_playgroup_id=uuid4())
    await app_schema.commit()
    assert (await _add(client, owner, group_id, "nia")).status_code == 201


async def test_a_409_leaves_the_existing_player_untouched(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    first = (await _add(client, owner, group_id, "José")).json()
    assert (await _add(client, owner, group_id, "JOSE")).status_code == 409
    assert await _name_of(app_schema, first["id"]) == "José"
    assert await _guest_count(app_schema) == 1


async def test_a_member_who_joined_later_blocks_the_name_too(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    await _member_named(app_schema, group_id, "Léo")
    response = await _add(client, owner, group_id, "LEO")
    assert_envelope(response, 409, "player_name_taken")


# --- criterion 3: rename ----------------------------------------------------------------------


async def _creator_setup(client: httpx.AsyncClient, app_schema: AppSession) -> dict[str, Any]:
    _, owner_player, owner = await make_user(app_schema)
    _, creator_player, creator = await make_user(app_schema)
    _, bystander_player, bystander = await make_user(app_schema)
    group_id = await _group(client, owner)
    await join(app_schema, group_id, creator_player)
    await join(app_schema, group_id, bystander_player)
    guest_id = (await _add(client, creator, group_id, "Ana")).json()["id"]
    return {
        "owner": owner,
        "owner_player": owner_player,
        "creator": creator,
        "creator_player": creator_player,
        "bystander": bystander,
        "bystander_player": bystander_player,
        "group_id": group_id,
        "guest_id": guest_id,
    }


async def test_a_rename_returns_the_updated_guest_and_keeps_its_other_fields(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    ctx = await _creator_setup(client, app_schema)
    before = await app_schema.get(Player, UUID(ctx["guest_id"]))
    assert before is not None
    color, created = before.avatar_color, before.created_at
    response = await _rename(client, ctx["creator"], ctx["group_id"], ctx["guest_id"], "Ana Maria")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == GUEST_KEYS
    assert body["id"] == ctx["guest_id"]
    assert body["display_name"] == "Ana Maria"
    assert body["playgroup_id"] == ctx["group_id"]
    assert body["created_by"] == str(ctx["creator_player"])
    assert body["avatar_color"] == color
    assert datetime.fromisoformat(body["created_at"]) == created
    row = await app_schema.get(Player, UUID(ctx["guest_id"]), populate_existing=True)
    assert row is not None
    assert (row.is_guest, row.user_id, row.home_playgroup_id) == (
        True,
        None,
        UUID(ctx["group_id"]),
    )
    assert row.created_by_id == ctx["creator_player"]


async def test_a_plain_member_who_did_not_add_the_guest_gets_403_and_the_name_is_unchanged(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    ctx = await _creator_setup(client, app_schema)
    response = await _rename(client, ctx["bystander"], ctx["group_id"], ctx["guest_id"], "Eve")
    assert_envelope(response, 403, "forbidden")
    assert await _name_of(app_schema, ctx["guest_id"]) == "Ana"


async def test_a_403_wins_over_a_409_so_a_bystander_cannot_probe_names(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    ctx = await _creator_setup(client, app_schema)
    await _add(client, ctx["owner"], ctx["group_id"], "Bea")
    response = await _rename(client, ctx["bystander"], ctx["group_id"], ctx["guest_id"], "BEA")
    assert_envelope(response, 403, "forbidden")


async def test_a_bystander_renames_only_the_guests_they_added_themselves(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    ctx = await _creator_setup(client, app_schema)
    own = (await _add(client, ctx["bystander"], ctx["group_id"], "Cat")).json()["id"]
    assert (await _rename(client, ctx["bystander"], ctx["group_id"], own, "Kat")).status_code == 200
    # The creator of "Ana" is just a member for this guest.
    response = await _rename(client, ctx["creator"], ctx["group_id"], own, "Cate")
    assert_envelope(response, 403, "forbidden")
    assert await _name_of(app_schema, own) == "Kat"


async def test_a_creator_demoted_from_admin_may_still_rename_the_guest_they_added(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    ctx = await _creator_setup(client, app_schema)
    await _set_role(app_schema, ctx["creator_player"], "admin")
    await _set_role(app_schema, ctx["creator_player"], "member")
    response = await _rename(client, ctx["creator"], ctx["group_id"], ctx["guest_id"], "Ana B")
    assert response.status_code == 200


async def test_a_creator_promoted_to_admin_may_rename_guests_added_by_others(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    ctx = await _creator_setup(client, app_schema)
    other = (await _add(client, ctx["owner"], ctx["group_id"], "Dee")).json()["id"]
    denied = await _rename(client, ctx["creator"], ctx["group_id"], other, "Dede")
    assert_envelope(denied, 403, "forbidden")
    await _set_role(app_schema, ctx["creator_player"], "admin")
    allowed = await _rename(client, ctx["creator"], ctx["group_id"], other, "Dede")
    assert allowed.status_code == 200
    assert await _name_of(app_schema, other) == "Dede"


async def test_a_member_who_lost_admin_loses_the_right_to_rename_other_members_guests(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    ctx = await _creator_setup(client, app_schema)
    await _set_role(app_schema, ctx["bystander_player"], "admin")
    assert (
        await _rename(client, ctx["bystander"], ctx["group_id"], ctx["guest_id"], "Ana2")
    ).status_code == 200
    await _set_role(app_schema, ctx["bystander_player"], "member")
    response = await _rename(client, ctx["bystander"], ctx["group_id"], ctx["guest_id"], "Ana3")
    assert_envelope(response, 403, "forbidden")
    assert await _name_of(app_schema, ctx["guest_id"]) == "Ana2"


async def test_an_admin_and_the_owner_may_rename_a_guest_they_did_not_add(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    ctx = await _creator_setup(client, app_schema)
    _, admin_player, admin = await make_user(app_schema)
    await join(app_schema, ctx["group_id"], admin_player, "admin")
    by_admin = await _rename(client, admin, ctx["group_id"], ctx["guest_id"], "By Admin")
    assert by_admin.status_code == 200
    assert await _name_of(app_schema, ctx["guest_id"]) == "By Admin"
    by_owner = await _rename(client, ctx["owner"], ctx["group_id"], ctx["guest_id"], "By Owner")
    assert by_owner.status_code == 200
    assert await _name_of(app_schema, ctx["guest_id"]) == "By Owner"


async def test_a_creator_who_left_the_playgroup_gets_404_and_the_name_is_unchanged(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    ctx = await _creator_setup(client, app_schema)
    await app_schema.execute(
        text("UPDATE memberships SET left_at = now() WHERE player_id = :p"),
        {"p": ctx["creator_player"]},
    )
    await app_schema.commit()
    response = await _rename(client, ctx["creator"], ctx["group_id"], ctx["guest_id"], "Eve")
    assert_envelope(response, 404, "not_found")
    assert await _name_of(app_schema, ctx["guest_id"]) == "Ana"
    # The owner still can, and the guest keeps its recorded creator.
    by_owner = await _rename(client, ctx["owner"], ctx["group_id"], ctx["guest_id"], "Anita")
    assert by_owner.status_code == 200
    row = await app_schema.get(Player, UUID(ctx["guest_id"]), populate_existing=True)
    assert row is not None
    assert row.created_by_id == ctx["creator_player"]


async def test_renaming_to_a_name_taken_by_a_member_or_another_guest_is_409_and_unchanged(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    ctx = await _creator_setup(client, app_schema)
    await _member_named(app_schema, ctx["group_id"], "Máx")
    await _add(client, ctx["owner"], ctx["group_id"], "Bea")
    for taken in ("max", "BÉA", "  bea "):
        response = await _rename(client, ctx["creator"], ctx["group_id"], ctx["guest_id"], taken)
        error = assert_envelope(response, 409, "player_name_taken")
        assert error["message"] == TAKEN.format(taken.strip())
    assert await _name_of(app_schema, ctx["guest_id"]) == "Ana"


async def test_renaming_a_guest_to_a_former_member_s_name_is_allowed(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    ctx = await _creator_setup(client, app_schema)
    await _member_named(app_schema, ctx["group_id"], "Old", left=True)
    response = await _rename(client, ctx["creator"], ctx["group_id"], ctx["guest_id"], "OLD")
    assert response.status_code == 200


@pytest.mark.parametrize("new_name", ["ana", "ANA", "Aná", " Ana ", "Ana"])
async def test_a_rename_that_only_changes_its_own_case_accents_or_padding_is_allowed(
    client: httpx.AsyncClient, app_schema: AppSession, new_name: str
) -> None:
    ctx = await _creator_setup(client, app_schema)
    response = await _rename(client, ctx["creator"], ctx["group_id"], ctx["guest_id"], new_name)
    assert response.status_code == 200
    assert response.json()["display_name"] == new_name.strip()
    assert await _name_of(app_schema, ctx["guest_id"]) == new_name.strip()


@pytest.mark.parametrize("name", ["", "   ", "x" * 33, "😀" * 33, None, 7])
async def test_a_rename_to_an_invalid_name_is_422_and_unchanged(
    client: httpx.AsyncClient, app_schema: AppSession, name: object
) -> None:
    ctx = await _creator_setup(client, app_schema)
    response = await _rename(client, ctx["creator"], ctx["group_id"], ctx["guest_id"], name)
    assert_envelope(response, 422, "validation_error")
    assert await _name_of(app_schema, ctx["guest_id"]) == "Ana"


@pytest.mark.parametrize("name", ["a", "x" * 32, "😀" * 32])
async def test_a_rename_accepts_1_to_32_characters(
    client: httpx.AsyncClient, app_schema: AppSession, name: str
) -> None:
    ctx = await _creator_setup(client, app_schema)
    response = await _rename(client, ctx["creator"], ctx["group_id"], ctx["guest_id"], name)
    assert response.status_code == 200
    assert await _name_of(app_schema, ctx["guest_id"]) == name


async def test_a_guest_of_another_playgroup_cannot_be_renamed_through_this_playgroup(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    ctx = await _creator_setup(client, app_schema)
    _, _, rival = await make_user(app_schema)
    rival_group = await _group(client, rival, "Rival")
    foreign = (await _add(client, rival, rival_group, "Fay")).json()["id"]
    # Even the owner of this playgroup, who is nobody in the rival group.
    response = await _rename(client, ctx["owner"], ctx["group_id"], foreign, "Hijack")
    assert_envelope(response, 404, "not_found")
    assert await _name_of(app_schema, foreign) == "Fay"
    # ... and the rival owner cannot rename "Ana" through their own URL either.
    response = await _rename(client, rival, rival_group, ctx["guest_id"], "Hijack")
    assert_envelope(response, 404, "not_found")
    assert await _name_of(app_schema, ctx["guest_id"]) == "Ana"


async def test_a_member_s_player_id_through_the_guest_route_is_never_renamed(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    ctx = await _creator_setup(client, app_schema)
    creator_name = await _name_of(app_schema, ctx["creator_player"])
    owner_name = await _name_of(app_schema, ctx["owner_player"])
    for headers in (ctx["owner"], ctx["creator"]):
        response = await _rename(client, headers, ctx["group_id"], ctx["creator_player"], "Pwned")
        assert response.status_code == 404
    response = await _rename(client, ctx["owner"], ctx["group_id"], ctx["owner_player"], "Pwned")
    assert response.status_code == 404
    assert await _name_of(app_schema, ctx["creator_player"]) == creator_name
    assert await _name_of(app_schema, ctx["owner_player"]) == owner_name


async def test_a_guest_without_a_recorded_creator_is_renamed_by_the_owner_not_by_a_member(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    # Rows older than the created_by_id column have no creator.
    ctx = await _creator_setup(client, app_schema)
    orphan = await create(
        app_schema,
        GuestPlayerFactory,
        display_name="Old Pal",
        home_playgroup_id=UUID(ctx["group_id"]),
        created_by_id=None,
    )
    orphan_id = orphan.id
    await app_schema.commit()
    denied = await _rename(client, ctx["creator"], ctx["group_id"], orphan_id, "X1")
    assert_envelope(denied, 403, "forbidden")
    allowed = await _rename(client, ctx["owner"], ctx["group_id"], orphan_id, "New Pal")
    assert allowed.status_code == 200


# --- criterion 4: no credentials --------------------------------------------------------------


async def test_login_as_a_guest_by_name_or_player_id_fails_like_an_unknown_user(
    api_app: FastAPI, client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    guest = (await _add(client, owner, group_id, "Ana Guest")).json()
    # A private client IP keeps the sign-in throttle out of the way.
    async with client_from(api_app, f"10.{uuid4().int % 250}.{uuid4().int % 250}.7") as http:
        unknown = await http.post(LOGIN, json={"username": "nobody_here", "password": "x" * 12})
        assert_envelope(unknown, 401, "invalid_credentials")
        for username in ("Ana Guest", "ana_guest", guest["id"]):
            for password in ("password1234", "correct horse battery"):
                response = await http.post(LOGIN, json={"username": username, "password": password})
                assert response.status_code in (401, 422), (username, password)
                assert "access_token" not in response.text
                assert "refresh_token" not in response.text
                assert "set-cookie" not in {k.lower() for k in response.headers}
                if response.status_code == 401:
                    assert response.json()["error"]["code"] == "invalid_credentials"


async def test_a_token_whose_subject_is_a_guest_s_player_id_authenticates_nothing(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    guest_id = UUID((await _add(client, owner, group_id, "Ana")).json()["id"])
    forged = {"Authorization": f"Bearer {issue(guest_id)}"}
    assert (await client.get("/api/v1/auth/me", headers=forged)).status_code == 401
    # Refused (401) or the unknown user is no member (404): either way it acts as nobody.
    assert (await _add(client, forged, group_id, "Bo")).status_code in (401, 404)
    assert (await _rename(client, forged, group_id, guest_id, "Eve")).status_code in (401, 404)
    assert await _name_of(app_schema, guest_id) == "Ana"
    assert await _guest_count(app_schema) == 1


async def test_a_refresh_token_naming_a_guest_has_nothing_to_rotate(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    guest_id = UUID((await _add(client, owner, group_id, "Ana")).json()["id"])
    # Signed with the real key but never stored, so there is no row to rotate.
    forged = issue(guest_id, "refresh")
    response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": forged}, headers={"X-Client": "mobile"}
    )
    assert response.status_code == 401
    assert "access_token" not in response.text


async def test_registering_with_a_guest_s_name_as_username_creates_a_new_user_only(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    guest = (await _add(client, owner, group_id, "ana_guest")).json()
    token = await make_token_invite(app_schema, await make_inviter(app_schema))
    users_before, players_before = await count_users(app_schema), await count_players(app_schema)

    response = await register(
        client, token, mobile=True, username="ana_guest", display_name="Somebody Else"
    )

    assert response.status_code == 201
    assert await count_users(app_schema) == users_before + 1
    assert await count_players(app_schema) == players_before + 1
    row = await app_schema.get(Player, UUID(guest["id"]), populate_existing=True)
    assert row is not None
    assert (row.user_id, row.is_guest, row.display_name) == (None, True, "ana_guest")
    new_user = (
        await app_schema.execute(select(User).where(User.username == "ana_guest"))
    ).scalar_one()
    attached = (
        await app_schema.execute(select(Player.id).where(Player.user_id == new_user.id))
    ).scalar_one()
    assert attached != UUID(guest["id"])


async def test_registering_a_user_whose_display_name_matches_a_guest_never_converts_the_guest(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    group_id = await _group(client, owner)
    guest = (await _add(client, owner, group_id, "Ana")).json()
    token = await make_token_invite(app_schema, await make_inviter(app_schema))
    response = await register(client, token, mobile=True, username="newana", display_name="Ana")
    assert response.status_code == 201
    row = await app_schema.get(Player, UUID(guest["id"]), populate_existing=True)
    assert row is not None
    assert row.user_id is None
    assert row.is_guest is True


async def test_the_database_refuses_a_user_on_a_guest_and_a_guest_flag_on_a_member(
    app_schema: AppSession,
) -> None:
    user = await create(app_schema, UserFactory)
    other = await create(app_schema, UserFactory)
    guest = await create(app_schema, GuestPlayerFactory)
    member = await create(app_schema, PlayerFactory, user_id=other.id)
    user_id, guest_id, member_id = user.id, guest.id, member.id
    await app_schema.commit()
    for statement, params in (
        ("UPDATE players SET user_id = :u WHERE id = :p", {"u": user_id, "p": guest_id}),
        ("UPDATE players SET is_guest = true WHERE id = :p", {"p": member_id}),
    ):
        savepoint = await app_schema.begin_nested()
        with pytest.raises(IntegrityError, match="guest_has_no_user"):
            await app_schema.execute(text(statement), params)
        await savepoint.rollback()


async def test_the_database_refuses_a_creator_that_is_not_a_player(app_schema: AppSession) -> None:
    savepoint = await app_schema.begin_nested()
    with pytest.raises(IntegrityError):
        await create(app_schema, GuestPlayerFactory, created_by_id=uuid4())
    await savepoint.rollback()


# --- OpenAPI ----------------------------------------------------------------------------------


async def test_the_openapi_document_lists_both_guest_routes_with_their_error_responses(
    client: httpx.AsyncClient,
) -> None:
    spec = (await client.get("/api/v1/openapi.json")).json()
    path = spec["paths"]["/api/v1/playgroups/{playgroup_id}/guests"]
    assert set(path) == {"post"}
    add = path["post"]
    assert add["tags"] == ["playgroups"]
    assert "201" in add["responses"]
    for status in ("401", "403", "404", "409", "422"):
        assert status in add["responses"], status
    assert add.get("security")
    rename_path = spec["paths"]["/api/v1/playgroups/{playgroup_id}/guests/{player_id}"]
    assert set(rename_path) == {"patch"}
    rename = rename_path["patch"]
    assert rename["tags"] == ["playgroups"]
    assert "200" in rename["responses"]
    for status in ("401", "403", "404", "409", "422"):
        assert status in rename["responses"], status
    assert rename.get("security")
