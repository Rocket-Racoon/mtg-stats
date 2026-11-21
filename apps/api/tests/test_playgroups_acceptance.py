"""Acceptance tests for S-2.4.1 (create a playgroup), API part, derived from the criteria.

AC1 create + owner, AC2 rename/describe permissions, AC3 non-members get 404, AC4 list with roles
and cursor paging, AC5 no cap on playgroups (decision C3). Database-level ownership rules are in
``test_playgroups_db_acceptance.py``.
"""

import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select

from app.core.db import AppSession
from app.playgroups.models import Membership
from tests.factories import PlaygroupFactory, create
from tests.playgroup_support import join, make_user
from tests.statement_counter import StatementCounter

pytestmark = pytest.mark.usefixtures("app_schema")
URL = "/api/v1/playgroups"
Headers = dict[str, str]


async def _create(client: httpx.AsyncClient, headers: Headers, **body: Any) -> httpx.Response:
    body.setdefault("name", "Friday Night")
    return await client.post(URL, json=body, headers=headers)


async def _group_for(session: AppSession, members: list[tuple[UUID, str]]) -> UUID:
    """A playgroup (factory) with the given ``(player_id, role)`` active memberships; committed."""
    group = await create(session, PlaygroupFactory)
    group_id = group.id
    await session.commit()
    for player_id, role in members:
        await join(session, group_id, player_id, role)
    return group_id


async def _many_groups(session: AppSession, player_id: UUID, count: int, role: str) -> set[UUID]:
    ids = set()
    for _ in range(count):
        ids.add(await _group_for(session, [(player_id, role)]))
    return ids


async def _all_items(client: httpx.AsyncClient, headers: Headers) -> list[dict[str, Any]]:
    response = await client.get(URL, params={"limit": 200}, headers=headers)
    assert response.status_code == 200
    return list(response.json()["items"])


def _shape(response: httpx.Response) -> tuple[int, dict[str, Any]]:
    """Status and body with the per-request ``request_id`` blanked, for indistinguishability."""
    body = response.json()
    body["error"]["request_id"] = "<request-id>"
    return response.status_code, body


# ------------------------------------------------------------------------------------- AC1


@pytest.mark.parametrize("length", [3, 4, 49, 50])
async def test_name_of_3_to_50_characters_creates_the_playgroup(
    client: httpx.AsyncClient, app_schema: AppSession, length: int
) -> None:
    _, _, headers = await make_user(app_schema)
    response = await _create(client, headers, name="n" * length)
    assert response.status_code == 201
    assert response.json()["name"] == "n" * length


@pytest.mark.parametrize("length", [0, 1, 2, 51, 100])
async def test_name_outside_3_to_50_characters_is_rejected_with_validation_error(
    client: httpx.AsyncClient, app_schema: AppSession, length: int
) -> None:
    _, _, headers = await make_user(app_schema)
    response = await _create(client, headers, name="n" * length)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"]
    assert error["request_id"]


async def test_a_missing_name_is_rejected_with_validation_error(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, headers = await make_user(app_schema)
    response = await client.post(URL, json={"description": "no name"}, headers=headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_a_rejected_create_stores_no_playgroup(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, headers = await make_user(app_schema)
    await _create(client, headers, name="ab")
    await _create(client, headers, description="d" * 501)
    assert await _all_items(client, headers) == []


async def test_the_description_is_optional_and_defaults_to_empty(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, headers = await make_user(app_schema)
    response = await _create(client, headers)
    assert response.status_code == 201
    assert response.json()["description"] == ""


async def test_a_description_of_500_characters_is_accepted_and_stored(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, headers = await make_user(app_schema)
    response = await _create(client, headers, description="d" * 500)
    assert response.status_code == 201
    assert response.json()["description"] == "d" * 500
    fetched = await client.get(f"{URL}/{response.json()['id']}", headers=headers)
    assert fetched.json()["description"] == "d" * 500


async def test_a_description_of_501_characters_is_rejected_with_validation_error(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, headers = await make_user(app_schema)
    response = await _create(client, headers, description="d" * 501)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"]


async def test_the_creator_is_the_owner_in_the_response_and_in_the_database(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, player_id, headers = await make_user(app_schema)
    response = await _create(client, headers, description="Commander")
    assert response.status_code == 201
    body = response.json()
    assert body["role"] == "owner"
    assert (body["name"], body["description"]) == ("Friday Night", "Commander")
    group_id = UUID(body["id"])
    rows = (
        (await app_schema.execute(select(Membership).where(Membership.playgroup_id == group_id)))
        .scalars()
        .all()
    )
    assert [(m.player_id, m.role, m.left_at) for m in rows] == [(player_id, "owner", None)]


async def test_creating_again_makes_a_distinct_playgroup_owned_by_the_creator_only(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, alice = await make_user(app_schema)
    _, _, bob = await make_user(app_schema)
    first = (await _create(client, alice)).json()
    second = (await _create(client, bob)).json()
    assert first["id"] != second["id"]
    assert [g["id"] for g in await _all_items(client, alice)] == [first["id"]]
    assert [g["id"] for g in await _all_items(client, bob)] == [second["id"]]


async def test_every_playgroup_route_without_a_token_is_401_unauthenticated(
    client: httpx.AsyncClient,
) -> None:
    some_id = str(uuid.uuid4())
    responses = [
        await client.post(URL, json={"name": "Friday"}),
        await client.get(URL),
        await client.get(f"{URL}/{some_id}"),
        await client.patch(f"{URL}/{some_id}", json={"name": "Friday"}),
    ]
    for response in responses:
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthenticated"


# ------------------------------------------------------------------------------------- AC2


@pytest.mark.parametrize("role", ["owner", "admin"])
async def test_owner_and_admin_can_rename_and_the_change_persists(
    client: httpx.AsyncClient, app_schema: AppSession, role: str
) -> None:
    _, player_id, headers = await make_user(app_schema)
    group_id = await _group_for(app_schema, [(player_id, role)])
    response = await client.patch(f"{URL}/{group_id}", json={"name": "New name"}, headers=headers)
    assert response.status_code == 200
    assert response.json()["name"] == "New name"
    fetched = await client.get(f"{URL}/{group_id}", headers=headers)
    assert fetched.json()["name"] == "New name"


@pytest.mark.parametrize("role", ["owner", "admin"])
async def test_owner_and_admin_can_edit_the_description_and_the_change_persists(
    client: httpx.AsyncClient, app_schema: AppSession, role: str
) -> None:
    _, player_id, headers = await make_user(app_schema)
    group_id = await _group_for(app_schema, [(player_id, role)])
    before = (await client.get(f"{URL}/{group_id}", headers=headers)).json()
    response = await client.patch(
        f"{URL}/{group_id}", json={"description": "Casual pods"}, headers=headers
    )
    assert response.status_code == 200
    fetched = (await client.get(f"{URL}/{group_id}", headers=headers)).json()
    assert fetched["description"] == "Casual pods"
    assert fetched["name"] == before["name"]


async def test_the_description_can_be_cleared_to_empty(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, headers = await make_user(app_schema)
    group_id = (await _create(client, headers, description="something")).json()["id"]
    response = await client.patch(f"{URL}/{group_id}", json={"description": ""}, headers=headers)
    assert response.status_code == 200
    assert (await client.get(f"{URL}/{group_id}", headers=headers)).json()["description"] == ""


async def test_name_and_description_can_be_changed_together(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, headers = await make_user(app_schema)
    group_id = (await _create(client, headers)).json()["id"]
    response = await client.patch(
        f"{URL}/{group_id}", json={"name": "Both", "description": "Changed"}, headers=headers
    )
    assert response.status_code == 200
    fetched = (await client.get(f"{URL}/{group_id}", headers=headers)).json()
    assert (fetched["name"], fetched["description"]) == ("Both", "Changed")


async def test_a_plain_member_gets_403_forbidden_and_nothing_changes(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, owner_player, owner = await make_user(app_schema)
    _, member_player, member = await make_user(app_schema)
    group_id = await _group_for(app_schema, [(owner_player, "owner"), (member_player, "member")])
    before = (await client.get(f"{URL}/{group_id}", headers=owner)).json()

    for body in ({"name": "Hijacked"}, {"description": "Hijacked"}):
        denied = await client.patch(f"{URL}/{group_id}", json=body, headers=member)
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "forbidden"

    after = (await client.get(f"{URL}/{group_id}", headers=owner)).json()
    assert after["name"] == before["name"]
    assert after["description"] == before["description"]


@pytest.mark.parametrize("role", ["owner", "admin"])
async def test_edit_name_length_limits_are_3_to_50(
    client: httpx.AsyncClient, app_schema: AppSession, role: str
) -> None:
    _, player_id, headers = await make_user(app_schema)
    group_id = await _group_for(app_schema, [(player_id, role)])
    for length in (2, 51):
        response = await client.patch(
            f"{URL}/{group_id}", json={"name": "n" * length}, headers=headers
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"
        assert response.json()["error"]["details"]
    for length in (3, 50):
        response = await client.patch(
            f"{URL}/{group_id}", json={"name": "n" * length}, headers=headers
        )
        assert response.status_code == 200
        assert response.json()["name"] == "n" * length


async def test_edit_description_length_limit_is_500(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, headers = await make_user(app_schema)
    group_id = (await _create(client, headers)).json()["id"]
    over = await client.patch(f"{URL}/{group_id}", json={"description": "d" * 501}, headers=headers)
    assert over.status_code == 422
    assert over.json()["error"]["code"] == "validation_error"
    exact = await client.patch(
        f"{URL}/{group_id}", json={"description": "d" * 500}, headers=headers
    )
    assert exact.status_code == 200
    assert (await client.get(f"{URL}/{group_id}", headers=headers)).json()["description"] == (
        "d" * 500
    )


async def test_a_rejected_edit_leaves_the_playgroup_unchanged(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, headers = await make_user(app_schema)
    created = (await _create(client, headers, description="keep")).json()
    await client.patch(f"{URL}/{created['id']}", json={"name": "ab"}, headers=headers)
    fetched = (await client.get(f"{URL}/{created['id']}", headers=headers)).json()
    assert (fetched["name"], fetched["description"]) == ("Friday Night", "keep")


# ------------------------------------------------------------------------------------- AC3


async def test_a_non_member_gets_404_not_found_from_every_playgroup_id_route(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, owner_player, _ = await make_user(app_schema)
    _, _, stranger = await make_user(app_schema)
    group_id = await _group_for(app_schema, [(owner_player, "owner")])

    for response in (
        await client.get(f"{URL}/{group_id}", headers=stranger),
        await client.patch(f"{URL}/{group_id}", json={"name": "Hijacked"}, headers=stranger),
    ):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


async def test_a_non_member_cannot_change_the_playgroup(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    _, _, stranger = await make_user(app_schema)
    group_id = (await _create(client, owner, description="mine")).json()["id"]
    await client.patch(f"{URL}/{group_id}", json={"name": "Hijacked"}, headers=stranger)
    fetched = (await client.get(f"{URL}/{group_id}", headers=owner)).json()
    assert (fetched["name"], fetched["description"]) == ("Friday Night", "mine")


async def test_an_invalid_edit_body_from_a_non_member_is_still_404(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, owner_player, _ = await make_user(app_schema)
    _, _, stranger = await make_user(app_schema)
    group_id = await _group_for(app_schema, [(owner_player, "owner")])
    response = await client.patch(f"{URL}/{group_id}", json={"name": "ab"}, headers=stranger)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize("method", ["GET", "PATCH"])
async def test_an_existing_playgroup_is_indistinguishable_from_a_nonexistent_one(
    client: httpx.AsyncClient, app_schema: AppSession, method: str
) -> None:
    _, owner_player, _ = await make_user(app_schema)
    _, _, stranger = await make_user(app_schema)
    existing = await _group_for(app_schema, [(owner_player, "owner")])
    body = {"name": "Hijacked"} if method == "PATCH" else None

    real = await client.request(method, f"{URL}/{existing}", json=body, headers=stranger)
    missing = await client.request(method, f"{URL}/{uuid.uuid4()}", json=body, headers=stranger)

    assert real.status_code == missing.status_code == 404
    assert _shape(real) == _shape(missing)
    assert dict(real.headers).keys() == dict(missing.headers).keys()


async def test_a_malformed_playgroup_id_is_404_not_found_too(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, headers = await make_user(app_schema)
    response = await client.get(f"{URL}/not-a-uuid", headers=headers)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_a_member_of_another_playgroup_is_still_a_non_member_here(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, owner_player, _ = await make_user(app_schema)
    _, other_player, other = await make_user(app_schema)
    group_id = await _group_for(app_schema, [(owner_player, "owner")])
    await _group_for(app_schema, [(other_player, "owner")])
    response = await client.get(f"{URL}/{group_id}", headers=other)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


# ------------------------------------------------------------------------------------- AC4


async def test_the_list_returns_every_playgroup_with_the_callers_role_and_no_one_elses(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, me, mine = await make_user(app_schema)
    _, other, theirs = await make_user(app_schema)
    owned = await _group_for(app_schema, [(me, "owner")])
    admined = await _group_for(app_schema, [(other, "owner"), (me, "admin")])
    member_of = await _group_for(app_schema, [(other, "owner"), (me, "member")])
    others_only = await _group_for(app_schema, [(other, "owner")])

    items = await _all_items(client, mine)
    assert {UUID(i["id"]): i["role"] for i in items} == {
        owned: "owner",
        admined: "admin",
        member_of: "member",
    }
    assert len(items) == 3
    others = {UUID(i["id"]): i["role"] for i in await _all_items(client, theirs)}
    assert others == {admined: "owner", member_of: "owner", others_only: "owner"}


async def test_the_list_items_carry_id_name_description_and_role(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, headers = await make_user(app_schema)
    created = (await _create(client, headers, name="Pod Squad", description="Tuesdays")).json()
    [item] = await _all_items(client, headers)
    assert item["id"] == created["id"]
    assert (item["name"], item["description"], item["role"]) == ("Pod Squad", "Tuesdays", "owner")


async def test_a_user_with_no_playgroups_gets_an_empty_page(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, headers = await make_user(app_schema)
    response = await client.get(URL, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}


async def test_paging_with_a_small_limit_visits_every_playgroup_exactly_once(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, me, headers = await make_user(app_schema)
    expected = await _many_groups(app_schema, me, 7, "member")

    seen: list[str] = []
    sizes: list[int] = []
    cursor: str | None = None
    for _ in range(10):  # a bound so a broken cursor cannot loop forever
        params: dict[str, Any] = {"limit": 3}
        if cursor is not None:
            params["cursor"] = cursor
        response = await client.get(URL, params=params, headers=headers)
        assert response.status_code == 200
        page = response.json()
        sizes.append(len(page["items"]))
        seen.extend(i["id"] for i in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert sizes == [3, 3, 1]
    assert len(seen) == len(set(seen)) == 7
    assert {UUID(s) for s in seen} == expected


async def test_a_page_that_exactly_fills_the_limit_on_the_last_page_has_no_next_cursor(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, me, headers = await make_user(app_schema)
    await _many_groups(app_schema, me, 4, "owner")
    first = (await client.get(URL, params={"limit": 2}, headers=headers)).json()
    assert first["next_cursor"] is not None
    second = (
        await client.get(URL, params={"limit": 2, "cursor": first["next_cursor"]}, headers=headers)
    ).json()
    assert len(second["items"]) == 2
    assert second["next_cursor"] is None


async def test_the_default_page_size_is_50(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, me, headers = await make_user(app_schema)
    await _many_groups(app_schema, me, 51, "member")
    page = (await client.get(URL, headers=headers)).json()
    assert len(page["items"]) == 50
    assert page["next_cursor"] is not None
    rest = (await client.get(URL, params={"cursor": page["next_cursor"]}, headers=headers)).json()
    assert len(rest["items"]) == 1
    assert rest["next_cursor"] is None


async def test_the_maximum_page_size_is_200(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, me, headers = await make_user(app_schema)
    await _many_groups(app_schema, me, 3, "member")
    assert (await client.get(URL, params={"limit": 200}, headers=headers)).status_code == 200
    for bad in (201, 0, -1):
        response = await client.get(URL, params={"limit": bad}, headers=headers)
        assert response.status_code == 422, bad
        assert response.json()["error"]["code"] == "validation_error"


async def test_an_invalid_cursor_is_422_invalid_cursor(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, headers = await make_user(app_schema)
    response = await client.get(URL, params={"cursor": "not-a-real-cursor"}, headers=headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_cursor"


async def test_a_playgroup_the_caller_has_not_joined_never_appears_in_their_paged_list(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, me, headers = await make_user(app_schema)
    _, other, _ = await make_user(app_schema)
    mine = await _many_groups(app_schema, me, 3, "member")
    await _many_groups(app_schema, other, 5, "owner")
    page = (await client.get(URL, params={"limit": 200}, headers=headers)).json()
    assert {UUID(i["id"]) for i in page["items"]} == mine


async def test_listing_runs_the_same_number_of_statements_for_1_and_for_8_playgroups(
    client: httpx.AsyncClient,
    app_schema: AppSession,
    count_statements: Callable[[], AbstractContextManager[StatementCounter]],
) -> None:
    _, me, headers = await make_user(app_schema)
    await _many_groups(app_schema, me, 1, "owner")
    with count_statements() as small:
        response = await client.get(URL, headers=headers)
    assert len(response.json()["items"]) == 1

    await _many_groups(app_schema, me, 7, "admin")
    with count_statements() as large:
        response = await client.get(URL, headers=headers)
    assert len(response.json()["items"]) == 8

    assert small.count == large.count, (small.statements, large.statements)


# ------------------------------------------------------------------------------------- AC5


async def test_a_user_who_owns_25_playgroups_can_create_another(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, headers = await make_user(app_schema)
    for i in range(25):
        assert (await _create(client, headers, name=f"Group {i:02d}")).status_code == 201
    response = await _create(client, headers, name="Group 25")
    assert response.status_code == 201
    assert response.json()["role"] == "owner"
    items = await _all_items(client, headers)
    assert len(items) == 26
    assert {i["role"] for i in items} == {"owner"}


async def test_a_user_who_belongs_to_25_playgroups_can_be_added_to_another_and_create_one(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, me, headers = await make_user(app_schema)
    _, other, _ = await make_user(app_schema)
    for _ in range(25):
        await _group_for(app_schema, [(other, "owner"), (me, "member")])

    joined = await _group_for(app_schema, [(other, "owner"), (me, "member")])
    created = await _create(client, headers)
    assert created.status_code == 201

    items = await _all_items(client, headers)
    assert len(items) == 27
    assert {UUID(i["id"]) for i in items} >= {joined, UUID(created.json()["id"])}
    assert (await client.get(f"{URL}/{joined}", headers=headers)).status_code == 200
