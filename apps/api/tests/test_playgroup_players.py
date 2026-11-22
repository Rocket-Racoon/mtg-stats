"""The playgroup player picker and roster, ``GET /playgroups/{id}/players`` (S-2.2.4)."""

import re
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select, text

from app.core.db import AppSession
from app.identity import services as identity_services
from app.identity.models import Player
from tests.auth_support import issue
from tests.factories import (
    GuestPlayerFactory,
    PlayerFactory,
    PlaygroupFactory,
    UserFactory,
    create,
)
from tests.playgroup_support import join, make_user
from tests.statement_counter import StatementCounter

pytestmark = pytest.mark.usefixtures("app_schema")
URL = "/api/v1/playgroups"


async def _group(session: AppSession, owner_player: UUID) -> UUID:
    group = await create(session, PlaygroupFactory)
    group_id = group.id
    await join(session, group_id, owner_player, "owner")
    return group_id


async def _member(
    session: AppSession, group_id: UUID, name: str, role: str = "member", *, active: bool = True
) -> UUID:
    user = await create(session, UserFactory, is_active=active)
    player = await create(session, PlayerFactory, user_id=user.id, display_name=name)
    player_id = player.id
    await join(session, group_id, player_id, role)
    return player_id


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


async def _get(
    client: httpx.AsyncClient, headers: dict[str, str], group_id: object, query: str = ""
) -> httpx.Response:
    return await client.get(f"{URL}/{group_id}/players{query}", headers=headers)


def _names(response: httpx.Response) -> list[str]:
    assert response.status_code == 200, response.text
    return [item["display_name"] for item in response.json()["items"]]


async def _setup(session: AppSession) -> tuple[UUID, UUID, dict[str, str]]:
    """A playgroup whose owner is ``Owner``; returns (group id, owner player id, headers)."""
    user = await create(session, UserFactory)
    owner = await create(session, PlayerFactory, user_id=user.id, display_name="Owner")
    owner_id = owner.id
    headers = {"Authorization": f"Bearer {issue(user.id)}"}
    await session.commit()
    return await _group(session, owner_id), owner_id, headers


# --- criterion 1: members first, then guests, alphabetical, with avatars ------------------------


async def test_picker_lists_10_members_then_3_guests_each_alphabetical_with_avatars(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, owner_id, headers = await _setup(app_schema)
    for name in ["Zed", "bea", "Álvaro", "alvaro2", "Mia", "carl", "Dana", "Eve", "Finn"]:
        await _member(app_schema, group_id, name)
    for name in ["Zoe guest", "adam guest", "Mo guest"]:
        await _guest(app_schema, group_id, name, owner_id)

    response = await _get(client, headers, group_id)

    assert _names(response) == [
        "Álvaro",
        "alvaro2",
        "bea",
        "carl",
        "Dana",
        "Eve",
        "Finn",
        "Mia",
        "Owner",
        "Zed",
        "adam guest",
        "Mo guest",
        "Zoe guest",
    ]
    body = response.json()
    assert body["next_cursor"] is None
    assert all(item["avatar_color"] for item in body["items"])
    assert [item["is_guest"] for item in body["items"]] == [False] * 10 + [True] * 3


async def test_order_ignores_case_and_accents_with_the_id_as_tie_break(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, _, headers = await _setup(app_schema)
    await _member(app_schema, group_id, "Bob")
    ids = [await _member(app_schema, group_id, n) for n in ["álvaro", "Alvaro"]]

    items = (await _get(client, headers, group_id)).json()["items"]

    # Same key "alvaro": they sit together, before "Bob", ordered by id.
    assert [i["id"] for i in items[:2]] == sorted(str(i) for i in ids)
    assert items[2]["display_name"] == "Bob"


async def test_items_carry_role_creator_and_never_email_or_username(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, owner_id, headers = await _setup(app_schema)
    admin = await _member(app_schema, group_id, "Adm", "admin")
    plain = await _member(app_schema, group_id, "Plain")
    guest = await _guest(app_schema, group_id, "Gus", plain)

    items = {i["id"]: i for i in (await _get(client, headers, group_id)).json()["items"]}

    assert items[str(owner_id)]["role"] == "owner"
    assert items[str(admin)]["role"] == "admin"
    assert items[str(plain)]["role"] == "member"
    assert items[str(plain)]["created_by"] is None
    assert items[str(guest)]["role"] is None
    assert items[str(guest)]["is_guest"] is True
    assert items[str(guest)]["created_by"] == str(plain)
    for item in items.values():
        assert set(item) == {"id", "display_name", "avatar_color", "is_guest", "role", "created_by"}


async def test_a_plain_member_may_call_it(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, _, _ = await _setup(app_schema)
    _, player_id, headers = await make_user(app_schema)
    await join(app_schema, group_id, player_id)
    assert "Owner" in _names(await _get(client, headers, group_id))


# --- who is listed ------------------------------------------------------------------------------


async def test_former_members_merged_guests_and_other_groups_guests_are_not_listed(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, owner_id, headers = await _setup(app_schema)
    other_group, other_owner, _ = await _setup(app_schema)
    former = await _member(app_schema, group_id, "Former")
    await app_schema.execute(
        text("UPDATE memberships SET left_at = now() WHERE player_id = :p"), {"p": former}
    )
    await app_schema.commit()
    survivor = await _member(app_schema, group_id, "Survivor")
    await _guest(app_schema, group_id, "Merged", owner_id, merged_into=survivor)
    await _guest(app_schema, other_group, "Elsewhere", other_owner)
    await _member(app_schema, other_group, "Stranger")
    await _guest(app_schema, group_id, "Kept", owner_id)

    assert _names(await _get(client, headers, group_id)) == ["Owner", "Survivor", "Kept"]


async def test_a_deactivated_account_that_is_still_a_member_is_listed(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, _, headers = await _setup(app_schema)
    await _member(app_schema, group_id, "Gone", active=False)
    assert "Gone" in _names(await _get(client, headers, group_id))


# --- criterion 2: search ------------------------------------------------------------------------


async def test_typing_jo_matches_word_starts_ignoring_case_and_accents(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, owner_id, headers = await _setup(app_schema)
    for name in ["Jöel", "JOSE", "Mary Jo", "Ajo", "Ann-Jo", "Bob"]:
        await _member(app_schema, group_id, name)
    await _guest(app_schema, group_id, "jon guest", owner_id)

    names = _names(await _get(client, headers, group_id, "?q=jo"))

    assert names == ["Ann-Jo", "Jöel", "JOSE", "Mary Jo", "jon guest"]


@pytest.mark.parametrize("q", ["JÖE", "joe", "  joe  ", "jo\u200bel"])
async def test_search_ignores_case_accents_spaces_and_invisible_characters(
    client: httpx.AsyncClient, app_schema: AppSession, q: str
) -> None:
    group_id, _, headers = await _setup(app_schema)
    await _member(app_schema, group_id, "Jöel")
    response = await client.get(f"{URL}/{group_id}/players", params={"q": q}, headers=headers)
    assert _names(response) == ["Jöel"]


async def test_search_matches_a_phrase_across_a_word_boundary(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, _, headers = await _setup(app_schema)
    await _member(app_schema, group_id, "Mary Jo Smith")
    response = await client.get(
        f"{URL}/{group_id}/players", params={"q": "jo smi"}, headers=headers
    )
    assert _names(response) == ["Mary Jo Smith"]


@pytest.mark.parametrize("q", ["%", "_", "\\", "a%", "%a", "a_c"])
async def test_like_wildcards_in_q_are_literal(
    client: httpx.AsyncClient, app_schema: AppSession, q: str
) -> None:
    group_id, _, headers = await _setup(app_schema)
    for name in ["abc", "a%c", "a_c", "100%"]:
        await _member(app_schema, group_id, name)
    response = await client.get(f"{URL}/{group_id}/players", params={"q": q}, headers=headers)
    # Word-start matches only: "100%" has "%" at the end, not at a word start.
    expected = {
        "%": [],
        "_": [],
        "\\": [],
        "a%": ["a%c"],
        "%a": [],
        "a_c": ["a_c"],
    }[q]
    assert _names(response) == expected


@pytest.mark.parametrize("q", ["", "   ", "\u200b", "\u200b \u200b"])
async def test_a_q_with_an_empty_key_lists_everyone(
    client: httpx.AsyncClient, app_schema: AppSession, q: str
) -> None:
    group_id, _, headers = await _setup(app_schema)
    await _member(app_schema, group_id, "Zed")
    response = await client.get(f"{URL}/{group_id}/players", params={"q": q}, headers=headers)
    assert _names(response) == ["Owner", "Zed"]


async def test_no_match_gives_an_empty_page(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, _, headers = await _setup(app_schema)
    response = await _get(client, headers, group_id, "?q=zzz")
    assert response.json() == {"items": [], "next_cursor": None}


async def test_a_q_longer_than_64_characters_is_a_422_validation_error(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, _, headers = await _setup(app_schema)
    ok = await _get(client, headers, group_id, "?q=" + "a" * 64)
    bad = await _get(client, headers, group_id, "?q=" + "a" * 65)
    assert ok.status_code == 200
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "validation_error"


async def test_a_q_with_a_control_character_is_a_422_validation_error(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, _, headers = await _setup(app_schema)
    for q in ("%00", "a%00b", "%0a"):
        response = await _get(client, headers, group_id, f"?q={q}")
        assert response.status_code == 422, q
        assert response.json()["error"]["code"] == "validation_error"


async def test_renaming_a_guest_moves_it_in_the_search(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, owner_id, headers = await _setup(app_schema)
    guest = await _guest(app_schema, group_id, "Zed", owner_id)
    assert _names(await _get(client, headers, group_id, "?q=adam")) == []
    renamed = await client.patch(
        f"{URL}/{group_id}/guests/{guest}", json={"display_name": "Ádam"}, headers=headers
    )
    assert renamed.status_code == 200
    assert _names(await _get(client, headers, group_id, "?q=adam")) == ["Ádam"]


# --- errors -------------------------------------------------------------------------------------


async def test_a_non_member_an_unknown_id_and_a_bad_id_are_the_same_404(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, _, _ = await _setup(app_schema)
    _, _, outsider = await make_user(app_schema)
    for target in (group_id, uuid4(), "not-a-uuid"):
        response = await _get(client, outsider, target)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


async def test_a_former_member_gets_404(client: httpx.AsyncClient, app_schema: AppSession) -> None:
    group_id, _, _ = await _setup(app_schema)
    _, player_id, headers = await make_user(app_schema)
    await join(app_schema, group_id, player_id, left=True)
    assert (await _get(client, headers, group_id)).status_code == 404


async def test_no_token_is_a_401(client: httpx.AsyncClient, app_schema: AppSession) -> None:
    group_id, _, _ = await _setup(app_schema)
    assert (await client.get(f"{URL}/{group_id}/players")).status_code == 401


@pytest.mark.parametrize("limit", ["0", "201", "-1", "x"])
async def test_a_bad_limit_is_a_422_validation_error(
    client: httpx.AsyncClient, app_schema: AppSession, limit: str
) -> None:
    group_id, _, headers = await _setup(app_schema)
    response = await _get(client, headers, group_id, f"?limit={limit}")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize(
    "cursor",
    [
        "x",
        "e30",  # {}
        "WyJwbGF5ZXJzOmcsayxpZCIsMiwiYSIsIngiXQ",  # group 2, id not a UUID
        "A" * 5000,
    ],
)
async def test_a_bad_cursor_is_a_422_invalid_cursor(
    client: httpx.AsyncClient, app_schema: AppSession, cursor: str
) -> None:
    group_id, _, headers = await _setup(app_schema)
    response = await _get(client, headers, group_id, f"?cursor={cursor}")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_cursor"


async def test_a_cursor_from_another_endpoint_is_a_422_invalid_cursor(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, _, headers = await _setup(app_schema)
    await client.post(URL, json={"name": "Second group"}, headers=headers)
    page = await client.get(f"{URL}?limit=1", headers=headers)
    cursor = page.json()["next_cursor"]
    assert cursor is not None
    response = await _get(client, headers, group_id, f"?cursor={cursor}")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_cursor"


# --- paging -------------------------------------------------------------------------------------


async def _walk(
    client: httpx.AsyncClient, headers: dict[str, str], group_id: UUID, limit: int, q: str = ""
) -> list[list[str]]:
    pages: list[list[str]] = []
    cursor: str | None = None
    while True:
        params: dict[str, str | int] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if q:
            params["q"] = q
        response = await client.get(f"{URL}/{group_id}/players", params=params, headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        pages.append([i["id"] for i in body["items"]])
        cursor = body["next_cursor"]
        if cursor is None:
            return pages


@pytest.mark.parametrize("limit", [1, 2, 3, 4, 13])
async def test_paging_across_the_member_guest_boundary_returns_every_player_once_in_order(
    client: httpx.AsyncClient, app_schema: AppSession, limit: int
) -> None:
    group_id, owner_id, headers = await _setup(app_schema)
    for name in ["m1", "m2", "m3", "m4", "m5"]:
        await _member(app_schema, group_id, name)
    for name in ["a guest", "b guest", "c guest", "d guest"]:
        await _guest(app_schema, group_id, name, owner_id)
    full = [i["id"] for i in (await _get(client, headers, group_id)).json()["items"]]
    assert len(full) == 10

    pages = await _walk(client, headers, group_id, limit)

    assert [i for page in pages for i in page] == full
    assert all(len(page) == limit for page in pages[:-1])


async def test_paging_with_equal_keys_uses_the_id_and_skips_nothing(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, _, headers = await _setup(app_schema)
    for name in ["Ana", "ANA", "Aña", "ana", "Anà"]:  # one key; uniqueness is the guest API's job
        await _member(app_schema, group_id, name)
    full = [i["id"] for i in (await _get(client, headers, group_id)).json()["items"]]
    pages = await _walk(client, headers, group_id, 2)
    assert [i for page in pages for i in page] == full
    assert len(set(full)) == 6


async def test_search_pages_too(client: httpx.AsyncClient, app_schema: AppSession) -> None:
    group_id, owner_id, headers = await _setup(app_schema)
    for i in range(5):
        await _member(app_schema, group_id, f"Jo m{i}")
        await _guest(app_schema, group_id, f"Jo g{i}", owner_id)
        await _member(app_schema, group_id, f"Other {i}")
    pages = await _walk(client, headers, group_id, 3, q="jo")
    assert [len(p) for p in pages] == [3, 3, 3, 1]


async def test_rows_added_between_pages_appear_only_if_they_sort_after_the_cursor(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, owner_id, headers = await _setup(app_schema)
    for name in ["b", "d", "f"]:
        await _member(app_schema, group_id, name)
    for name in ["x", "z"]:
        await _guest(app_schema, group_id, name, owner_id)
    first = (await _get(client, headers, group_id, "?limit=3")).json()
    assert [i["display_name"] for i in first["items"]] == ["b", "d", "f"]

    await _member(app_schema, group_id, "a")  # before the cursor: not shown on page two
    await _guest(app_schema, group_id, "y", owner_id)  # after it: shown
    await _member(app_schema, group_id, "zz member")  # a member sorts before every guest

    second = await _get(client, headers, group_id, f"?limit=10&cursor={first['next_cursor']}")
    assert _names(second) == ["Owner", "zz member", "x", "y", "z"]


async def test_a_renamed_row_never_makes_the_scan_skip_or_repeat_the_others(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, owner_id, headers = await _setup(app_schema)
    guests = {n: await _guest(app_schema, group_id, n, owner_id) for n in "abcdef"}
    first = (await _get(client, headers, group_id, "?limit=3")).json()  # Owner, a, b
    await client.patch(
        f"{URL}/{group_id}/guests/{guests['a']}", json={"display_name": "zz"}, headers=headers
    )
    rest = await _get(client, headers, group_id, f"?limit=50&cursor={first['next_cursor']}")
    assert _names(rest) == ["c", "d", "e", "f", "zz"]


# --- scale, statements, timing ------------------------------------------------------------------


async def _fill(
    session: AppSession, group_id: UUID, owner_id: UUID, members: int, guests: int
) -> None:
    users = [UserFactory.build() for _ in range(members)]
    session.add_all(users)
    await session.flush()
    session.add_all(
        PlayerFactory.build(user_id=u.id, display_name=f"Member {i:04d} Jöel")
        for i, u in enumerate(users)
    )
    await session.flush()
    session.add_all(
        GuestPlayerFactory.build(
            display_name=f"Guest {i:04d}", home_playgroup_id=group_id, created_by_id=owner_id
        )
        for i in range(guests)
    )
    await session.flush()
    await session.execute(
        text(
            "INSERT INTO memberships (id, playgroup_id, player_id, role, joined_at) "
            "SELECT gen_random_uuid(), :g, p.id, 'member', now() FROM players p "
            "WHERE p.display_name LIKE 'Member %'"
        ),
        {"g": group_id},
    )
    await session.commit()


async def test_statement_count_is_constant_and_the_call_is_fast_for_a_large_playgroup(
    client: httpx.AsyncClient,
    app_schema: AppSession,
    count_statements: Callable[[], AbstractContextManager[StatementCounter]],
) -> None:
    group_id, owner_id, headers = await _setup(app_schema)
    with count_statements() as small:
        assert (await _get(client, headers, group_id, "?limit=50")).status_code == 200
    await _fill(app_schema, group_id, owner_id, members=600, guests=300)

    with count_statements() as large:
        started = time.perf_counter()
        response = await _get(client, headers, group_id, "?limit=200")
        elapsed = time.perf_counter() - started
    assert len(response.json()["items"]) == 200
    with count_statements() as searched:
        started_q = time.perf_counter()
        found = await _get(client, headers, group_id, "?q=joel&limit=50")
        elapsed_q = time.perf_counter() - started_q

    assert large.count == small.count == searched.count
    assert len(found.json()["items"]) == 50
    # The criterion's budget is 200 ms; this is that budget, measured in-process.
    assert elapsed < 0.2, elapsed
    assert elapsed_q < 0.2, elapsed_q


# --- the stored key never drifts from the function ----------------------------------------------


async def test_stored_keys_equal_the_python_function_on_every_write_path(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    group_id, _, headers = await _setup(app_schema)
    await identity_services.create_user_with_player(
        app_schema, username="keyuser", password="correct horse battery 9", display_name="Zoë  Ng"
    )
    added = await client.post(
        f"{URL}/{group_id}/guests", json={"display_name": "  ÁLVARO  "}, headers=headers
    )
    renamed = await client.patch(
        f"{URL}/{group_id}/guests/{added.json()['id']}",
        json={"display_name": "Éa\u200bm"},
        headers=headers,
    )
    assert renamed.status_code == 200
    await identity_services.create_guest_player(app_schema, display_name="Straße")

    rows = (await app_schema.execute(select(Player.display_name, Player.display_name_key))).all()
    assert len(rows) >= 4
    for display_name, key in rows:
        assert key == identity_services.display_name_key(display_name), display_name
    assert {k for _, k in rows} >= {"zoe ng", "eam", "strasse"}


def test_nothing_outside_the_identity_services_assigns_a_display_name() -> None:
    """The stored key is kept right by the identity services; a stray assignment would drift."""
    root = Path(__file__).resolve().parents[1] / "app"
    pattern = re.compile(r"\.display_name\s*=[^=]")
    offenders = [
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if "tests" not in path.parts
        and path.name != "players.py"
        and pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
