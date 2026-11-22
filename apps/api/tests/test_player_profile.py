"""``GET /players/{id}``: the player profile view (S-2.2.3)."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import update

from app.core.db import AppSession
from app.identity.models import Player
from tests.factories import GuestPlayerFactory, PlaygroupFactory, create
from tests.playgroup_support import join, make_user
from tests.statement_counter import StatementCounter

pytestmark = pytest.mark.usefixtures("app_schema")
URL = "/api/v1/players"


async def _group(session: AppSession, name: str = "Friday Night") -> UUID:
    group = await create(session, PlaygroupFactory, name=name)
    group_id = group.id
    await session.commit()
    return group_id


async def test_a_shared_playgroup_member_sees_the_profile_and_the_groups_in_common(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, viewer, headers = await make_user(app_schema)
    _, other, _ = await make_user(app_schema)
    shared, only_other = await _group(app_schema, "Shared"), await _group(app_schema, "Theirs")
    await join(app_schema, shared, viewer)
    await join(app_schema, shared, other)
    await join(app_schema, only_other, other)
    response = await client.get(f"{URL}/{other}", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "id",
        "display_name",
        "avatar_color",
        "is_guest",
        "member_since",
        "common_playgroups",
    }
    assert body["id"] == str(other)
    assert body["is_guest"] is False
    assert body["common_playgroups"] == [{"id": str(shared), "name": "Shared"}]


async def test_a_stranger_and_an_unknown_player_get_the_same_404(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, headers = await make_user(app_schema)
    _, stranger, _ = await make_user(app_schema)
    await join(app_schema, await _group(app_schema), stranger)
    hidden = await client.get(f"{URL}/{stranger}", headers=headers)
    missing = await client.get(f"{URL}/{uuid4()}", headers=headers)
    garbage = await client.get(f"{URL}/not-a-uuid", headers=headers)
    assert hidden.status_code == missing.status_code == garbage.status_code == 404
    errors = [r.json()["error"] for r in (hidden, missing, garbage)]
    assert {(e["code"], e["message"]) for e in errors} == {("not_found", errors[0]["message"])}


async def test_a_former_member_is_not_a_shared_playgroup(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, viewer, headers = await make_user(app_schema)
    _, other, _ = await make_user(app_schema)
    group = await _group(app_schema)
    await join(app_schema, group, viewer)
    await join(app_schema, group, other, left=True)
    assert (await client.get(f"{URL}/{other}", headers=headers)).status_code == 404


async def test_the_profile_needs_a_token(client: httpx.AsyncClient) -> None:
    assert (await client.get(f"{URL}/{uuid4()}")).status_code == 401


async def test_a_viewer_can_open_their_own_profile_without_any_playgroup(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, me, headers = await make_user(app_schema)
    response = await client.get(f"{URL}/{me}", headers=headers)
    assert response.status_code == 200
    assert response.json()["common_playgroups"] == []


async def test_a_guest_of_a_playgroup_the_viewer_belongs_to_is_visible_with_a_badge(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, viewer, headers = await make_user(app_schema)
    group, elsewhere = await _group(app_schema, "Home"), await _group(app_schema, "Other")
    await join(app_schema, group, viewer)
    visible = await create(app_schema, GuestPlayerFactory, home_playgroup_id=group)
    hidden = await create(app_schema, GuestPlayerFactory, home_playgroup_id=elsewhere)
    homeless = await create(app_schema, GuestPlayerFactory)
    ids = (visible.id, hidden.id, homeless.id)
    await app_schema.commit()
    ok = await client.get(f"{URL}/{ids[0]}", headers=headers)
    assert ok.status_code == 200
    assert ok.json()["is_guest"] is True
    assert ok.json()["common_playgroups"] == [{"id": str(group), "name": "Home"}]
    assert (await client.get(f"{URL}/{ids[1]}", headers=headers)).status_code == 404
    assert (await client.get(f"{URL}/{ids[2]}", headers=headers)).status_code == 404


async def test_a_merged_guest_redirects_to_the_survivor_through_a_chain(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, viewer, headers = await make_user(app_schema)
    _, survivor, _ = await make_user(app_schema)
    group = await _group(app_schema)
    await join(app_schema, group, viewer)
    await join(app_schema, group, survivor)
    middle = await create(app_schema, GuestPlayerFactory, merged_into_id=survivor)
    old = await create(app_schema, GuestPlayerFactory, merged_into_id=middle.id)
    old_id = old.id
    await app_schema.commit()
    response = await client.get(f"{URL}/{old_id}", headers=headers, follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == f"{URL}/{survivor}"
    assert response.json() == {"merged_into_id": str(survivor)}
    followed = await client.get(f"{URL}/{old_id}", headers=headers, follow_redirects=True)
    assert followed.status_code == 200
    assert followed.json()["id"] == str(survivor)


async def test_a_merged_guest_whose_survivor_is_hidden_is_a_404(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, headers = await make_user(app_schema)
    _, survivor, _ = await make_user(app_schema)
    old = await create(app_schema, GuestPlayerFactory, merged_into_id=survivor)
    old_id = old.id
    await app_schema.commit()
    response = await client.get(f"{URL}/{old_id}", headers=headers, follow_redirects=False)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_a_merge_loop_is_a_404_and_terminates(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, headers = await make_user(app_schema)
    a = await create(app_schema, GuestPlayerFactory)
    b = await create(app_schema, GuestPlayerFactory, merged_into_id=a.id)
    await app_schema.execute(update(Player).where(Player.id == a.id).values(merged_into_id=b.id))
    a_id = a.id
    await app_schema.commit()
    assert (await client.get(f"{URL}/{a_id}", headers=headers)).status_code == 404


async def test_the_common_playgroups_come_from_a_constant_number_of_queries(
    client: httpx.AsyncClient,
    app_schema: AppSession,
    count_statements: Callable[[], AbstractContextManager[StatementCounter]],
) -> None:
    _, viewer, headers = await make_user(app_schema)
    _, other, _ = await make_user(app_schema)

    async def measure() -> int:
        with count_statements() as counter:
            assert (await client.get(f"{URL}/{other}", headers=headers)).status_code == 200
        return counter.count

    for i in range(2):
        group = await _group(app_schema, f"Group {i}")
        await join(app_schema, group, viewer)
        await join(app_schema, group, other)
    few = await measure()
    for i in range(2, 10):
        group = await _group(app_schema, f"Group {i}")
        await join(app_schema, group, viewer)
        await join(app_schema, group, other)
    assert await measure() == few
