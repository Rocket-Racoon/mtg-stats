"""Acceptance tests for S-2.2.4 "Pick players from my playgroup" (API part).

Written from the criteria (members first then guests, alphabetical, with avatars; "jo" matches
"Jöel" ignoring case and accents, fast, via ``GET /playgroups/{id}/players?q=``).
``test_playgroup_players.py`` holds the implementer's checks; these add the exact scenario, who is
listed, matching edge cases, paging and stability guarantees, access, statement counts, timing,
the stored key and the OpenAPI document.

Matching rule under test (the criteria only give a prefix example): a name matches when its key
starts with the query's key, or the query's key follows a space or a hyphen in it.
"""

import json
import sys
import time
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text

from app.core.db import AppSession
from app.identity import services as identity_services
from app.identity.models import Player
from app.playgroups.models import Membership
from tests.auth_support import issue
from tests.factories import (
    GuestPlayerFactory,
    PlayerFactory,
    PlaygroupFactory,
    UserFactory,
    create,
)
from tests.playgroup_support import join, make_user
from tests.register_support import make_inviter, make_token_invite, register
from tests.statement_counter import StatementCounter

pytestmark = pytest.mark.usefixtures("app_schema")
URL = "/api/v1/playgroups"
ITEM_KEYS = {"id", "display_name", "avatar_color", "is_guest", "role", "created_by"}
Counter = Callable[[], AbstractContextManager[StatementCounter]]


# --- helpers ------------------------------------------------------------------------------------


async def _setup(
    session: AppSession, owner_name: str = "Owner"
) -> tuple[UUID, UUID, dict[str, str]]:
    """A playgroup owned by a player called ``owner_name``; (group id, owner player id, headers)."""
    user = await create(session, UserFactory)
    owner = await create(session, PlayerFactory, user_id=user.id, display_name=owner_name)
    owner_id = owner.id
    headers = {"Authorization": f"Bearer {issue(user.id)}"}
    group = await create(session, PlaygroupFactory)
    group_id = group.id
    await session.commit()
    await join(session, group_id, owner_id, "owner")
    return group_id, owner_id, headers


async def _member(
    session: AppSession, group_id: UUID, name: str, role: str = "member"
) -> tuple[UUID, dict[str, str]]:
    user = await create(session, UserFactory)
    player = await create(session, PlayerFactory, user_id=user.id, display_name=name)
    player_id = player.id
    headers = {"Authorization": f"Bearer {issue(user.id)}"}
    await join(session, group_id, player_id, role)
    return player_id, headers


async def _guest(
    session: AppSession,
    group_id: UUID,
    name: str,
    created_by: UUID | None = None,
    merged_into: UUID | None = None,
) -> UUID:
    guest = await create(
        session,
        GuestPlayerFactory,
        display_name=name,
        home_playgroup_id=group_id,
        created_by_id=created_by,
        merged_into_id=merged_into,
    )
    guest_id = guest.id
    await session.commit()
    return guest_id


async def _bulk(
    session: AppSession,
    group_id: UUID,
    owner_id: UUID,
    member_names: Sequence[str],
    guest_names: Sequence[str],
) -> list[UUID]:
    """Insert many members and guests in a few statements; returns the member ids."""
    users = [UserFactory.build() for _ in member_names]
    session.add_all(users)
    await session.flush()
    members = [
        PlayerFactory.build(user_id=u.id, display_name=n)
        for u, n in zip(users, member_names, strict=True)
    ]
    session.add_all(members)
    session.add_all(
        GuestPlayerFactory.build(display_name=n, home_playgroup_id=group_id, created_by_id=owner_id)
        for n in guest_names
    )
    await session.flush()
    session.add_all(
        Membership(playgroup_id=group_id, player_id=m.id, role="member") for m in members
    )
    ids = [m.id for m in members]
    await session.commit()
    return ids


async def _get(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    group_id: object,
    **params: str | int,
) -> httpx.Response:
    return await client.get(f"{URL}/{group_id}/players", params=params, headers=headers)


def _names(response: httpx.Response) -> list[str]:
    assert response.status_code == 200, response.text
    return [item["display_name"] for item in response.json()["items"]]


async def _search(
    client: httpx.AsyncClient, headers: dict[str, str], group_id: UUID, q: str
) -> list[str]:
    return _names(await _get(client, headers, group_id, q=q))


async def _walk(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    group_id: UUID,
    limit: int | None,
    q: str = "",
) -> list[list[str]]:
    pages: list[list[str]] = []
    cursor: str | None = None
    while True:
        params: dict[str, str | int] = {}
        if limit is not None:
            params["limit"] = limit
        if cursor:
            params["cursor"] = cursor
        if q:
            params["q"] = q
        response = await _get(client, headers, group_id, **params)
        assert response.status_code == 200, response.text
        body = response.json()
        pages.append([i["id"] for i in body["items"]])
        cursor = body["next_cursor"]
        if cursor is None:
            return pages
        assert len(pages) < 1000


def _without_request_id(response: httpx.Response) -> dict[str, Any]:
    body: dict[str, Any] = response.json()
    body["error"].pop("request_id")
    return body


# --- criterion 1: the story's scenario ----------------------------------------------------------


async def test_story_scenario_10_members_and_3_guests_members_first_each_alphabetical(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, owner_id, owner_headers = await _setup(app_schema, "Frank")
    # Created in scrambled order; the owner "Frank" makes member 10.
    plain = {}
    for name in ["Zed", "carl", "Émile", "Mia", "álvaro", "Dana", "Eve", "Alvaro", "bea"]:
        role = "admin" if name == "Zed" else "member"
        plain[name] = await _member(app_schema, group_id, name, role)
    guest_a = await _guest(app_schema, group_id, "zoe the guest", plain["Mia"][0])
    guest_b = await _guest(app_schema, group_id, "Ångström", owner_id)
    guest_c = await _guest(app_schema, group_id, "moe", plain["Zed"][0])

    response = await _get(client, owner_headers, group_id)

    assert response.status_code == 200
    body = response.json()
    assert body["next_cursor"] is None
    items = body["items"]
    assert len(items) == 13
    # "álvaro" and "Alvaro" share one key: they sit together, ordered by id.
    pair = sorted([plain["álvaro"][0], plain["Alvaro"][0]], key=str)
    assert [i["id"] for i in items[:2]] == [str(p) for p in pair]
    assert [i["display_name"] for i in items[2:10]] == [
        "bea",
        "carl",
        "Dana",
        "Émile",
        "Eve",
        "Frank",
        "Mia",
        "Zed",
    ]
    assert [i["display_name"] for i in items[10:]] == ["Ångström", "moe", "zoe the guest"]
    assert [i["is_guest"] for i in items] == [False] * 10 + [True] * 3
    assert all(isinstance(i["avatar_color"], str) and i["avatar_color"] for i in items)
    by_id = {i["id"]: i for i in items}
    assert by_id[str(owner_id)]["role"] == "owner"
    assert by_id[str(plain["Zed"][0])]["role"] == "admin"
    assert by_id[str(plain["Mia"][0])]["role"] == "member"
    for guest_id, creator in [(guest_a, plain["Mia"][0]), (guest_b, owner_id)]:
        assert by_id[str(guest_id)]["role"] is None
        assert by_id[str(guest_id)]["created_by"] == str(creator)
    assert by_id[str(guest_c)]["created_by"] == str(plain["Zed"][0])
    assert by_id[str(owner_id)]["created_by"] is None


async def test_every_item_has_exactly_the_documented_keys_and_leaks_nothing_else(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, _, headers = await _setup(app_schema)
    member_id, _ = await _member(app_schema, group_id, "Mem")
    await _guest(app_schema, group_id, "Gus", member_id)
    user_rows = (await app_schema.execute(text("SELECT username, email, id FROM users"))).all()

    response = await _get(client, headers, group_id)

    for item in response.json()["items"]:
        assert set(item) == ITEM_KEYS
    for username, email, user_id in user_rows:
        assert str(user_id) not in response.text
        assert email not in response.text
        assert username not in response.text
    assert str(group_id) not in response.text


async def test_the_caller_appears_in_their_own_list(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, _, _ = await _setup(app_schema)
    member_id, headers = await _member(app_schema, group_id, "Me Myself")
    ids = [i["id"] for i in (await _get(client, headers, group_id)).json()["items"]]
    assert str(member_id) in ids


async def test_listed_ids_are_the_player_ids_other_endpoints_use(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, owner_id, headers = await _setup(app_schema)
    member_id, _ = await _member(app_schema, group_id, "Mem")
    await _guest(app_schema, group_id, "Gus", owner_id)
    items = {i["display_name"]: i for i in (await _get(client, headers, group_id)).json()["items"]}

    for name in ("Mem", "Gus"):
        profile = await client.get(f"/api/v1/players/{items[name]['id']}", headers=headers)
        assert profile.status_code == 200
        assert profile.json()["id"] == items[name]["id"]
        assert profile.json()["display_name"] == name
    assert items["Mem"]["id"] == str(member_id)
    renamed = await client.patch(
        f"{URL}/{group_id}/guests/{items['Gus']['id']}",
        json={"display_name": "Gustav"},
        headers=headers,
    )
    assert renamed.status_code == 200


async def test_order_is_code_point_order_of_the_key_with_space_and_hyphen_before_letters(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, _, headers = await _setup(app_schema, "Zzz Owner")
    for name in ["Anabel", "Ana-B", "Ana Z", "\U0001f600 smile", "Ñu", "Nz", "9 lives", "Åke"]:
        await _member(app_schema, group_id, name)

    assert _names(await _get(client, headers, group_id)) == [
        "9 lives",
        "Åke",
        "Ana Z",
        "Ana-B",
        "Anabel",
        "Ñu",
        "Nz",
        "Zzz Owner",
        "\U0001f600 smile",
    ]


# --- who is listed ------------------------------------------------------------------------------


async def test_a_member_who_left_disappears_and_one_who_rejoined_appears_once(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, _, headers = await _setup(app_schema)
    left_id, _ = await _member(app_schema, group_id, "Leaver")
    back_id, _ = await _member(app_schema, group_id, "Boomerang")
    await app_schema.execute(
        text("UPDATE memberships SET left_at = now() WHERE player_id IN (:a, :b)"),
        {"a": left_id, "b": back_id},
    )
    await app_schema.commit()
    await join(app_schema, group_id, back_id)

    assert _names(await _get(client, headers, group_id)) == ["Boomerang", "Owner"]


async def test_a_guest_merged_into_a_member_is_gone_and_the_member_stays(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, owner_id, headers = await _setup(app_schema)
    member_id, _ = await _member(app_schema, group_id, "Real Ana")
    await _guest(app_schema, group_id, "Ana (guest)", owner_id, merged_into=member_id)

    assert _names(await _get(client, headers, group_id)) == ["Owner", "Real Ana"]
    assert await _search(client, headers, group_id, "ana") == ["Real Ana"]


async def test_other_playgroups_members_and_guests_never_appear_in_either_direction(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_a, owner_a, headers_a = await _setup(app_schema, "Owner A")
    group_b, owner_b, headers_b = await _setup(app_schema, "Owner B")
    await _member(app_schema, group_a, "Member A")
    await _member(app_schema, group_b, "Member B")
    await _guest(app_schema, group_a, "Guest A", owner_a)
    await _guest(app_schema, group_b, "Guest B", owner_b)

    assert _names(await _get(client, headers_a, group_a)) == ["Member A", "Owner A", "Guest A"]
    assert _names(await _get(client, headers_b, group_b)) == ["Member B", "Owner B", "Guest B"]
    assert await _search(client, headers_a, group_a, "b") == []


async def test_a_player_in_two_playgroups_appears_in_both_with_each_groups_own_role(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_a, _, _ = await _setup(app_schema, "Owner A")
    group_b, _, _ = await _setup(app_schema, "Owner B")
    shared_id, shared_headers = await _member(app_schema, group_a, "Shared", "admin")
    await join(app_schema, group_b, shared_id, "member")

    for group_id, role in ((group_a, "admin"), (group_b, "member")):
        items = (await _get(client, shared_headers, group_id)).json()["items"]
        mine = [i for i in items if i["id"] == str(shared_id)]
        assert len(mine) == 1
        assert mine[0]["role"] == role


async def test_a_guest_added_through_the_api_is_listed_and_found(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, owner_id, headers = await _setup(app_schema)
    added = await client.post(
        f"{URL}/{group_id}/guests", json={"display_name": "Nuevo Ámigo"}, headers=headers
    )
    assert added.status_code == 201

    items = (await _get(client, headers, group_id, q="amigo")).json()["items"]
    assert [(i["id"], i["is_guest"], i["role"], i["created_by"]) for i in items] == [
        (added.json()["id"], True, None, str(owner_id))
    ]
    assert _names(await _get(client, headers, group_id)) == ["Owner", "Nuevo Ámigo"]


async def test_a_renamed_guest_is_found_by_the_new_name_and_not_the_old_one(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, owner_id, headers = await _setup(app_schema)
    guest_id = await _guest(app_schema, group_id, "Old Name", owner_id)
    renamed = await client.patch(
        f"{URL}/{group_id}/guests/{guest_id}", json={"display_name": "Émile"}, headers=headers
    )
    assert renamed.status_code == 200

    assert await _search(client, headers, group_id, "emile") == ["Émile"]
    assert await _search(client, headers, group_id, "old") == []
    assert _names(await _get(client, headers, group_id)) == ["Owner", "Émile"]


# --- criterion 2: matching ----------------------------------------------------------------------


@pytest.mark.parametrize("q", ["jo", "JO", "Jó", " jo ", "j\u200do", "jo\u0301"])
async def test_jo_in_any_case_with_accents_spaces_or_invisible_characters_finds_the_names(
    client: httpx.AsyncClient, app_schema: AppSession, q: str
) -> None:
    group_id, owner_id, headers = await _setup(app_schema)
    for name in ["Jöel", "joel", "JOSÉ", "Bob"]:
        await _member(app_schema, group_id, name)
    await _guest(app_schema, group_id, "Ana", owner_id)

    found = await _search(client, headers, group_id, q)

    assert sorted(found) == ["JOSÉ", "Jöel", "joel"]


async def test_a_decomposed_query_finds_a_precomposed_name_and_the_reverse(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, _, headers = await _setup(app_schema)
    await _member(app_schema, group_id, "Jöel")  # precomposed
    await _member(app_schema, group_id, "Jo\u0308rg")  # decomposed

    assert await _search(client, headers, group_id, "Jo\u0308") == ["Jöel", "Jo\u0308rg"]
    assert await _search(client, headers, group_id, "Jö") == ["Jöel", "Jo\u0308rg"]
    assert await _search(client, headers, group_id, "joe") == ["Jöel"]


async def test_jo_matches_a_word_start_after_a_space_or_hyphen_but_not_the_middle_of_a_word(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, _, headers = await _setup(app_schema)
    for name in ["Mary Jo", "Ann-Jo", "Ajo", "Bojo", "Jo"]:
        await _member(app_schema, group_id, name)

    assert await _search(client, headers, group_id, "jo") == ["Ann-Jo", "Jo", "Mary Jo"]


async def test_a_multi_word_query_matches_across_the_space(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, _, headers = await _setup(app_schema)
    for name in ["Mary Jo", "Mary Jane", "Mary Ann", "Larry Jo"]:
        await _member(app_schema, group_id, name)

    assert await _search(client, headers, group_id, "mary j") == ["Mary Jane", "Mary Jo"]
    assert await _search(client, headers, group_id, "mary  JO") == ["Mary Jo"]
    assert await _search(client, headers, group_id, "ry jo") == []


async def test_the_search_covers_members_and_guests_and_keeps_members_first(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, owner_id, headers = await _setup(app_schema)
    await _guest(app_schema, group_id, "Joaquín", owner_id)
    await _guest(app_schema, group_id, "adam jo", owner_id)
    await _member(app_schema, group_id, "Zed Jones")
    await _member(app_schema, group_id, "Josh")

    response = await _get(client, headers, group_id, q="JO")

    assert _names(response) == ["Josh", "Zed Jones", "adam jo", "Joaquín"]
    assert [i["is_guest"] for i in response.json()["items"]] == [False, False, True, True]


async def test_a_query_with_no_match_is_an_empty_page_not_an_error(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, _, headers = await _setup(app_schema)
    response = await _get(client, headers, group_id, q="qqqq")
    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}


TRICKY = ["50% off", "a_b", "back\\slash", "O'Neil", 'Say "hi"', "x'; DROP TABLE players;--", "abc"]


@pytest.mark.parametrize(
    ("q", "expected"),
    [
        ("%", []),  # nobody's name starts with a percent sign
        ("5", ["50% off"]),
        ("50%", ["50% off"]),
        ("50% o", ["50% off"]),
        ("50%o", []),
        ("%0", []),
        ("a_", ["a_b"]),
        ("a_b", ["a_b"]),
        ("_", []),
        ("_b", []),
        ("a__", []),
        ("back\\", ["back\\slash"]),
        ("\\", []),
        ("\\s", []),
        ("o'", ["O'Neil"]),
        ('say "h', ['Say "hi"']),
        ("x'; drop", ["x'; DROP TABLE players;--"]),
        ("'", []),
        ('"', ['Say "hi"']),  # a word start: the name's second word begins with a quote
        ('"h', ['Say "hi"']),
        ("hi", []),
        ("a%b", []),
        ("%%", []),
    ],
)
async def test_wildcards_and_quotes_in_q_are_literal_and_nothing_matches_by_accident(
    client: httpx.AsyncClient, app_schema: AppSession, q: str, expected: list[str]
) -> None:
    group_id, _, headers = await _setup(app_schema, "Zed")
    for name in TRICKY:
        await _member(app_schema, group_id, name)

    assert await _search(client, headers, group_id, q) == expected
    assert (await app_schema.execute(text("SELECT count(*) FROM players"))).scalar_one() == 8


async def test_a_name_that_really_starts_with_a_percent_sign_matches_a_percent_query(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, _, headers = await _setup(app_schema)
    await _member(app_schema, group_id, "%special")
    await _member(app_schema, group_id, "Plain")
    assert await _search(client, headers, group_id, "%") == ["%special"]
    assert await _search(client, headers, group_id, "%s") == ["%special"]


async def test_emoji_and_non_latin_names_match_and_sort_by_code_point(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, owner_id, headers = await _setup(app_schema)
    for name in ["\U0001f600 Happy", "Жанна", "田中", "Σίσυφος", "Ελένη"]:
        await _member(app_schema, group_id, name)
    await _guest(app_schema, group_id, "شادي", owner_id)

    assert await _search(client, headers, group_id, "\U0001f600") == ["\U0001f600 Happy"]
    assert await _search(client, headers, group_id, "жа") == ["Жанна"]
    assert await _search(client, headers, group_id, "ЖАН") == ["Жанна"]
    assert await _search(client, headers, group_id, "田") == ["田中"]
    assert await _search(client, headers, group_id, "σισ") == ["Σίσυφος"]  # noqa: RUF001
    assert await _search(client, headers, group_id, "ش") == ["شادي"]
    assert _names(await _get(client, headers, group_id)) == [
        "Owner",
        "Ελένη",
        "Σίσυφος",
        "Жанна",
        "田中",
        "\U0001f600 Happy",
        "شادي",
    ]


async def test_casefolding_matches_sharp_s(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, _, headers = await _setup(app_schema)
    await _member(app_schema, group_id, "Straße")
    assert await _search(client, headers, group_id, "strasse") == ["Straße"]
    assert await _search(client, headers, group_id, "STRASSE") == ["Straße"]


async def test_a_query_with_a_hyphen_matches_inside_a_hyphenated_name(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, _, headers = await _setup(app_schema)
    await _member(app_schema, group_id, "Anne-Marie")
    await _member(app_schema, group_id, "Jean Marie")
    assert await _search(client, headers, group_id, "anne-m") == ["Anne-Marie"]
    assert await _search(client, headers, group_id, "marie") == ["Anne-Marie", "Jean Marie"]


# --- paging -------------------------------------------------------------------------------------


async def test_a_page_ending_exactly_between_the_last_member_and_the_first_guest(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, owner_id, headers = await _setup(app_schema)
    for name in ["m1", "m2", "m3"]:
        await _member(app_schema, group_id, name)
    for name in ["g1", "g2", "g3", "g4"]:
        await _guest(app_schema, group_id, name, owner_id)

    first = (await _get(client, headers, group_id, limit=4)).json()
    assert [i["display_name"] for i in first["items"]] == ["m1", "m2", "m3", "Owner"]
    assert first["next_cursor"] is not None
    second = (await _get(client, headers, group_id, limit=4, cursor=first["next_cursor"])).json()
    assert [i["display_name"] for i in second["items"]] == ["g1", "g2", "g3", "g4"]
    assert second["next_cursor"] is None


async def test_next_cursor_is_null_exactly_when_the_page_holds_the_last_row(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, owner_id, headers = await _setup(app_schema)
    for name in ["a", "b"]:
        await _member(app_schema, group_id, name)
    await _guest(app_schema, group_id, "c", owner_id)  # 4 players in all

    assert (await _get(client, headers, group_id, limit=4)).json()["next_cursor"] is None
    assert (await _get(client, headers, group_id, limit=5)).json()["next_cursor"] is None
    three = await _get(client, headers, group_id, limit=3)
    assert three.json()["next_cursor"] is not None
    last = await _get(client, headers, group_id, limit=3, cursor=three.json()["next_cursor"])
    assert _names(last) == ["c"]
    assert last.json()["next_cursor"] is None


async def test_limit_1_200_and_the_default_50_page_sizes(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, owner_id, headers = await _setup(app_schema)
    await _bulk(
        app_schema,
        group_id,
        owner_id,
        [f"M {i:03d}" for i in range(104)],
        [f"G {i:03d}" for i in range(100)],
    )  # 205 players with the owner

    assert len((await _get(client, headers, group_id, limit=1)).json()["items"]) == 1
    page200 = (await _get(client, headers, group_id, limit=200)).json()
    assert len(page200["items"]) == 200
    assert page200["next_cursor"] is not None
    default = (await _get(client, headers, group_id)).json()
    assert len(default["items"]) == 50
    assert default["next_cursor"] is not None
    pages = await _walk(client, headers, group_id, None)
    assert [len(p) for p in pages] == [50, 50, 50, 50, 5]
    flat = [i for page in pages for i in page]
    assert len(flat) == len(set(flat)) == 205


@pytest.mark.parametrize("limit", ["0", "201", "-5", "abc", "1.5", ""])
async def test_a_limit_outside_1_to_200_or_not_a_number_is_a_422_validation_error(
    client: httpx.AsyncClient, app_schema: AppSession, limit: str
) -> None:
    group_id, _, headers = await _setup(app_schema)
    response = await client.get(f"{URL}/{group_id}/players?limit={limit}", headers=headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_walking_a_group_with_many_equal_keys_returns_each_row_once_in_order(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, owner_id, headers = await _setup(app_schema)
    variants = ["ana", "ANA", "Aná", "Ána", "aNa", "A\u200bna"]
    await _bulk(
        app_schema,
        group_id,
        owner_id,
        [variants[i % 6] for i in range(24)],
        [variants[i % 6] for i in range(12)],
    )
    full = (await _get(client, headers, group_id, limit=200)).json()["items"]
    members, guests = full[:25], full[25:]
    assert [m["is_guest"] for m in members] == [False] * 25
    ana_members = [m["id"] for m in members if m["display_name"] != "Owner"]
    assert ana_members == sorted(ana_members)
    assert [g["id"] for g in guests] == sorted(g["id"] for g in guests)

    for limit in (1, 5, 7, 12):
        pages = await _walk(client, headers, group_id, limit)
        flat = [i for page in pages for i in page]
        assert flat == [i["id"] for i in full], limit


async def test_walking_names_with_emoji_and_non_ascii_survives_the_cursor_round_trip(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, owner_id, headers = await _setup(app_schema)
    await _bulk(
        app_schema,
        group_id,
        owner_id,
        ["\U0001f600 a", "Жанна", "田中", "O'Neil \\ %_", 'q"uote'],
        ["\U0001f680 g", "ñandú"],
    )
    full = [i["id"] for i in (await _get(client, headers, group_id)).json()["items"]]
    pages = await _walk(client, headers, group_id, 1)
    assert [i for page in pages for i in page] == full
    assert len(full) == 8


async def test_paging_and_searching_together_return_every_match_once(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, owner_id, headers = await _setup(app_schema)
    await _bulk(
        app_schema,
        group_id,
        owner_id,
        [f"Jo m{i:02d}" for i in range(7)] + [f"Other {i}" for i in range(7)],
        [f"Mary Jo g{i:02d}" for i in range(6)] + ["Nope"],
    )
    full = (await _get(client, headers, group_id, q="jo", limit=200)).json()["items"]
    assert len(full) == 13
    assert [i["is_guest"] for i in full] == [False] * 7 + [True] * 6

    pages = await _walk(client, headers, group_id, 4, q="jo")

    assert [len(p) for p in pages] == [4, 4, 4, 1]
    assert [i for page in pages for i in page] == [i["id"] for i in full]


async def test_garbage_and_truncated_cursors_are_422_invalid_cursor(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, owner_id, headers = await _setup(app_schema)
    await _guest(app_schema, group_id, "g", owner_id)
    good = (await _get(client, headers, group_id, limit=1)).json()["next_cursor"]
    assert good

    for bad in ("not-a-cursor", "!!!", good[: len(good) // 2], good[:-1] + "!", "%20", "null"):
        response = await _get(client, headers, group_id, cursor=bad)
        assert response.status_code == 422, bad
        assert response.json()["error"]["code"] == "invalid_cursor", bad


async def test_a_cursor_from_another_q_is_just_a_position_and_gives_a_sane_page(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    """Documented behaviour: the cursor holds only a position, so it is valid for any ``q``."""
    group_id, _, headers = await _setup(app_schema, "Owner")
    for name in ["Bea", "Carl", "Dana", "Jo", "Joan", "Zed"]:
        await _member(app_schema, group_id, name)
    cursor = (await _get(client, headers, group_id, limit=3)).json()["next_cursor"]  # after Carl

    response = await _get(client, headers, group_id, q="jo", cursor=cursor)

    assert _names(response) == ["Jo", "Joan"]


async def test_a_cursor_from_another_playgroup_never_leaks_that_groups_players(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_a, _, headers_a = await _setup(app_schema, "Owner A")
    group_b, _, headers_b = await _setup(app_schema, "Owner B")
    for name in ["A1", "A2", "A3"]:
        await _member(app_schema, group_a, name)
    await _member(app_schema, group_b, "B9")
    cursor = (await _get(client, headers_a, group_a, limit=1)).json()["next_cursor"]

    response = await _get(client, headers_b, group_b, cursor=cursor)

    assert _names(response) == ["B9", "Owner B"]


# --- stability between pages --------------------------------------------------------------------


async def test_a_row_added_before_the_cursor_is_not_shown_and_one_after_it_is(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, owner_id, headers = await _setup(app_schema, "Owner")
    for name in ["b", "d", "f"]:
        await _member(app_schema, group_id, name)
    first = (await _get(client, headers, group_id, limit=3)).json()
    assert [i["display_name"] for i in first["items"]] == ["b", "d", "f"]

    await _member(app_schema, group_id, "a")  # before the cursor
    await _member(app_schema, group_id, "g")  # after it
    await _guest(app_schema, group_id, "guest", owner_id)

    second = await _get(client, headers, group_id, limit=50, cursor=first["next_cursor"])
    assert _names(second) == ["g", "Owner", "guest"]


async def test_removing_a_member_between_pages_skips_and_repeats_no_unchanged_row(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, owner_id, headers = await _setup(app_schema, "Owner")
    ids = {n: (await _member(app_schema, group_id, n))[0] for n in "bcdefg"}
    await _guest(app_schema, group_id, "gst", owner_id)
    first = (await _get(client, headers, group_id, limit=3)).json()
    assert [i["display_name"] for i in first["items"]] == ["b", "c", "d"]

    for victim in ("b", "e"):  # one already shown, one still to come
        await app_schema.execute(
            text("UPDATE memberships SET left_at = now() WHERE player_id = :p"),
            {"p": ids[victim]},
        )
    await app_schema.commit()

    rest = await _get(client, headers, group_id, limit=50, cursor=first["next_cursor"])
    assert _names(rest) == ["f", "g", "Owner", "gst"]


async def test_renaming_a_row_between_pages_never_skips_or_repeats_the_others(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, owner_id, headers = await _setup(app_schema, "Owner")
    guests = {n: await _guest(app_schema, group_id, n, owner_id) for n in "abcdefg"}
    first = (await _get(client, headers, group_id, limit=4)).json()  # Owner, a, b, c
    for old, new in (("a", "zz"), ("e", "bb")):
        renamed = await client.patch(
            f"{URL}/{group_id}/guests/{guests[old]}", json={"display_name": new}, headers=headers
        )
        assert renamed.status_code == 200

    rest = await _get(client, headers, group_id, limit=50, cursor=first["next_cursor"])

    # "bb" now sorts before the cursor (after "b", before "c"): not shown. "d", "f", "g" untouched.
    assert _names(rest) == ["d", "f", "g", "zz"]


# --- access -------------------------------------------------------------------------------------


async def test_every_kind_of_outsider_gets_the_same_404_even_with_bad_parameters(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, _, _ = await _setup(app_schema)
    _, _, other_headers = await _setup(app_schema, "Other owner")  # a member elsewhere
    _, _, stranger = await make_user(app_schema)
    _, former_player, former = await make_user(app_schema)
    await join(app_schema, group_id, former_player, left=True)

    responses = [
        await _get(client, stranger, group_id),
        await _get(client, former, group_id),
        await _get(client, other_headers, group_id),
        await _get(client, stranger, uuid4()),
        await _get(client, stranger, "not-a-uuid"),
        # The 404 comes before parameter validation.
        await _get(client, stranger, group_id, limit=0),
        await _get(client, other_headers, group_id, q="x" * 100),
        await _get(client, former, group_id, limit=500, cursor="garbage"),
    ]

    assert {r.status_code for r in responses} == {404}
    bodies = [_without_request_id(r) for r in responses]
    assert all(b == bodies[0] for b in bodies)
    assert bodies[0]["error"]["code"] == "not_found"


async def test_no_token_and_a_bad_token_are_401(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, _, _ = await _setup(app_schema)
    missing = await client.get(f"{URL}/{group_id}/players")
    bad = await client.get(
        f"{URL}/{group_id}/players", headers={"Authorization": "Bearer not.a.token"}
    )
    assert missing.status_code == bad.status_code == 401
    assert missing.json()["error"]["code"] == "unauthenticated"
    assert bad.json()["error"]["code"] == "invalid_token"
    unauth_bad_params = await client.get(f"{URL}/{group_id}/players?limit=0")
    assert unauth_bad_params.status_code == 401


async def test_owner_admin_and_plain_member_all_get_the_same_list(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, owner_id, owner_headers = await _setup(app_schema)
    _, admin_headers = await _member(app_schema, group_id, "Admin", "admin")
    _, member_headers = await _member(app_schema, group_id, "Plain")
    await _guest(app_schema, group_id, "Gus", owner_id)

    lists = [
        (await _get(client, h, group_id)).json()
        for h in (owner_headers, admin_headers, member_headers)
    ]

    assert lists[0] == lists[1] == lists[2]
    assert len(lists[0]["items"]) == 4


# --- statement counts and speed -----------------------------------------------------------------


async def test_statement_count_is_the_same_for_5_and_300_players_with_and_without_q_and_on_page_2(
    client: httpx.AsyncClient, app_schema: AppSession, count_statements: Counter
) -> None:
    group_id, owner_id, headers = await _setup(app_schema)
    for name in ["Jo a", "Bea"]:
        await _member(app_schema, group_id, name)
    await _guest(app_schema, group_id, "Jo g", owner_id)
    await _guest(app_schema, group_id, "Gus", owner_id)  # 5 players with the owner
    await _get(client, headers, group_id)  # warm up

    async def measure() -> list[int]:
        counts = []
        for params in ({"limit": 3}, {"limit": 3, "q": "jo"}, {"limit": 200}):
            with count_statements() as counter:
                response = await _get(client, headers, group_id, **params)
            assert response.status_code == 200
            counts.append(counter.count)
        first = (await _get(client, headers, group_id, limit=2)).json()
        with count_statements() as counter:
            page2 = await _get(client, headers, group_id, limit=2, cursor=first["next_cursor"])
        assert page2.status_code == 200
        counts.append(counter.count)
        return counts

    small = await measure()
    await _bulk(
        app_schema,
        group_id,
        owner_id,
        [f"Jo member {i:03d}" for i in range(150)],
        [f"Jo guest {i:03d}" for i in range(145)],
    )
    large = await measure()

    assert small == large
    assert len(set(small)) == 1  # q, limit and the cursor do not change the count either


async def test_300_players_answer_well_under_200_ms(
    client: httpx.AsyncClient,
    app_schema: AppSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    group_id, owner_id, headers = await _setup(app_schema)
    await _bulk(
        app_schema,
        group_id,
        owner_id,
        [f"Member {i:03d} Jöel" for i in range(150)],
        [f"Guest {i:03d} Jo" for i in range(149)],
    )
    await _get(client, headers, group_id)  # warm connection and plans

    timings: dict[str, float] = {}
    for label, params in (
        ("default page", {}),
        ("limit 200", {"limit": 200}),
        ("q=jo", {"q": "jo"}),
        ("q=jö limit 200", {"q": "jö", "limit": 200}),
    ):
        runs = []
        for _ in range(7):
            started = time.perf_counter()
            response = await _get(client, headers, group_id, **params)
            runs.append(time.perf_counter() - started)
            assert response.status_code == 200
        timings[label] = min(runs) * 1000
    with capsys.disabled():
        sys.stdout.write(f"\nbest-of-7 route times (ms): {timings}\n")

    assert all(ms < 200 for ms in timings.values()), timings


# --- the stored key -----------------------------------------------------------------------------


async def test_the_stored_key_equals_the_function_after_register_guest_add_and_guest_rename(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, _, headers = await _setup(app_schema)
    inviter = await make_inviter(app_schema)
    token = await make_token_invite(app_schema, inviter)
    registered = await register(
        client, token, username="keycheck", display_name="  Zoë  ÉMILE\u200b  "
    )
    assert registered.status_code == 201
    added = await client.post(
        f"{URL}/{group_id}/guests", json={"display_name": "ÁLVARO  de  la  Vega"}, headers=headers
    )
    assert added.status_code == 201
    renamed = await client.patch(
        f"{URL}/{group_id}/guests/{added.json()['id']}",
        json={"display_name": "Zo\u200bë  MIXED  Case"},
        headers=headers,
    )
    assert renamed.status_code == 200

    rows = (await app_schema.execute(select(Player.display_name, Player.display_name_key))).all()

    assert len(rows) >= 4
    for display_name, stored in rows:
        assert stored == identity_services.display_name_key(display_name), repr(display_name)
    stored_keys = {stored for _, stored in rows}
    assert "zoe emile" in stored_keys
    assert "zoe mixed case" in stored_keys
    assert "alvaro de la vega" not in stored_keys  # the rename replaced the old key


def test_the_key_function_ignores_case_accents_invisibles_and_extra_spaces() -> None:
    f = identity_services.display_name_key
    assert f("Jöel") == f("JOEL") == f("Jo\u0308el") == f("jo\u200bel") == "joel"
    assert f("  Mary   Jo ") == "mary jo"
    assert f("Straße") == "strasse"
    assert f("\u0301\u200b") == ""
    assert f("Ann-Jo") == "ann-jo"


async def test_guest_name_uniqueness_uses_the_same_key_as_the_search(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, _, headers = await _setup(app_schema)
    first = await client.post(
        f"{URL}/{group_id}/guests", json={"display_name": "Émile"}, headers=headers
    )
    assert first.status_code == 201
    clash = await client.post(
        f"{URL}/{group_id}/guests", json={"display_name": "e\u200bmile"}, headers=headers
    )
    assert clash.status_code == 409
    assert clash.json()["error"]["code"] == "player_name_taken"
    assert await _search(client, headers, group_id, "emile") == ["Émile"]


# --- OpenAPI ------------------------------------------------------------------------------------


def _resolve(spec: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    if "$ref" in schema:
        node: Any = spec
        for part in schema["$ref"].removeprefix("#/").split("/"):
            node = node[part]
        return dict(node)
    return schema


async def test_openapi_lists_the_route_with_its_query_parameters_and_response_schema(
    api_app: FastAPI,
) -> None:
    spec = api_app.openapi()
    operation = spec["paths"]["/api/v1/playgroups/{playgroup_id}/players"]["get"]

    params = {p["name"]: p for p in operation["parameters"] if p["in"] == "query"}
    assert set(params) == {"q", "cursor", "limit"}
    assert params["q"]["required"] is False
    assert '"maxLength": 64' in json.dumps(params["q"])
    limit = params["limit"]["schema"]
    assert (limit["minimum"], limit["maximum"], limit["default"]) == (1, 200, 50)
    assert [p["name"] for p in operation["parameters"] if p["in"] == "path"] == ["playgroup_id"]
    assert operation["security"]

    ok = operation["responses"]["200"]["content"]["application/json"]["schema"]
    page = _resolve(spec, ok)
    assert set(page["properties"]) == {"items", "next_cursor"}
    item = _resolve(spec, page["properties"]["items"]["items"])
    assert set(item["properties"]) == ITEM_KEYS
    assert not {"email", "username", "user_id", "home_playgroup_id"} & set(item["properties"])
