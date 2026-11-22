"""Acceptance tests for S-2.2.3 "View a player's profile" (``GET /api/v1/players/{id}``)."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text, update
from sqlalchemy.exc import DBAPIError

from app.core.db import AppSession
from app.identity.models import Player
from tests.auth_support import claims_for, encode, issue, tamper_signature
from tests.factories import GuestPlayerFactory, PlaygroupFactory, create
from tests.playgroup_support import join, make_user
from tests.statement_counter import StatementCounter

pytestmark = pytest.mark.usefixtures("app_schema")
URL = "/api/v1/players"
KEYS = {"id", "display_name", "avatar_color", "is_guest", "member_since", "common_playgroups"}
HOP_LIMIT_PLUS = 15


async def _group(session: AppSession, name: str = "Friday Night") -> UUID:
    group = await create(session, PlaygroupFactory, name=name)
    group_id = group.id
    await session.commit()
    return group_id


async def _guest(session: AppSession, **fields: Any) -> UUID:
    guest = await create(session, GuestPlayerFactory, **fields)
    guest_id = guest.id
    await session.commit()
    return guest_id


def _error(response: httpx.Response) -> dict[str, Any]:
    error: dict[str, Any] = dict(response.json()["error"])
    error.pop("request_id")
    return error


def _header_names(response: httpx.Response) -> set[str]:
    return {k.lower() for k in response.headers} - {"x-request-id", "date"}


# ---------------------------------------------------------------- criterion 1


async def test_both_members_of_a_shared_playgroup_see_each_others_profile(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, ada, ada_headers = await make_user(app_schema)
    _, bob, bob_headers = await make_user(app_schema)
    group = await _group(app_schema, "Shared")
    await join(app_schema, group, ada)
    await join(app_schema, group, bob)
    for headers, target in ((ada_headers, bob), (bob_headers, ada)):
        response = await client.get(f"{URL}/{target}", headers=headers)
        assert response.status_code == 200
        assert response.json()["id"] == str(target)


async def test_the_profile_carries_every_promised_field_with_its_value_and_type(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, viewer, headers = await make_user(app_schema)
    _, other, _ = await make_user(app_schema)
    group = await _group(app_schema, "Shared")
    await join(app_schema, group, viewer)
    await join(app_schema, group, other)
    created = datetime(2024, 3, 5, 12, 30, tzinfo=UTC)
    await app_schema.execute(
        update(Player)
        .where(Player.id == other)
        .values(display_name="Zed the Brave", avatar_color="teal", created_at=created)
    )
    await app_schema.commit()
    body = (await client.get(f"{URL}/{other}", headers=headers)).json()
    assert set(body) == KEYS
    assert body["id"] == str(other)
    assert body["display_name"] == "Zed the Brave"
    assert body["avatar_color"] == "teal"
    assert body["is_guest"] is False
    assert datetime.fromisoformat(body["member_since"]) == created
    assert body["common_playgroups"] == [{"id": str(group), "name": "Shared"}]


async def test_is_guest_is_true_for_a_guest_and_false_for_a_member(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, viewer, headers = await make_user(app_schema)
    _, member, _ = await make_user(app_schema)
    group = await _group(app_schema)
    await join(app_schema, group, viewer)
    await join(app_schema, group, member)
    guest = await _guest(app_schema, home_playgroup_id=group, display_name="Wandering Guest")
    as_member = (await client.get(f"{URL}/{member}", headers=headers)).json()
    as_guest = (await client.get(f"{URL}/{guest}", headers=headers)).json()
    assert as_member["is_guest"] is False
    assert as_guest["is_guest"] is True
    assert as_guest["display_name"] == "Wandering Guest"
    assert set(as_guest) == KEYS
    assert isinstance(as_guest["avatar_color"], str)
    assert as_guest["avatar_color"]


async def test_common_playgroups_lists_only_the_shared_ones_and_leaks_no_other_group(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, viewer, headers = await make_user(app_schema)
    _, other, _ = await make_user(app_schema)
    shared = await _group(app_schema, "Shared")
    viewers_only = await _group(app_schema, "ViewerSecretGroup")
    others_only = await _group(app_schema, "TargetSecretGroup")
    await join(app_schema, shared, viewer)
    await join(app_schema, shared, other)
    await join(app_schema, viewers_only, viewer)
    await join(app_schema, others_only, other)
    response = await client.get(f"{URL}/{other}", headers=headers)
    assert response.json()["common_playgroups"] == [{"id": str(shared), "name": "Shared"}]
    assert "TargetSecretGroup" not in response.text
    assert str(others_only) not in response.text
    assert "ViewerSecretGroup" not in response.text


async def test_several_shared_playgroups_are_all_listed_ordered_by_name(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, viewer, headers = await make_user(app_schema)
    _, other, _ = await make_user(app_schema)
    ids = {}
    for name in ("Charlie", "Alpha", "Bravo"):
        ids[name] = await _group(app_schema, name)
        await join(app_schema, ids[name], viewer)
        await join(app_schema, ids[name], other)
    body = (await client.get(f"{URL}/{other}", headers=headers)).json()
    assert body["common_playgroups"] == [
        {"id": str(ids[n]), "name": n} for n in ("Alpha", "Bravo", "Charlie")
    ]


async def test_a_playgroup_the_target_has_left_is_not_listed_while_another_still_is(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, viewer, headers = await make_user(app_schema)
    _, other, _ = await make_user(app_schema)
    kept, gone = await _group(app_schema, "Kept"), await _group(app_schema, "Gone")
    for group in (kept, gone):
        await join(app_schema, group, viewer)
    await join(app_schema, kept, other)
    await join(app_schema, gone, other, left=True)
    body = (await client.get(f"{URL}/{other}", headers=headers)).json()
    assert body["common_playgroups"] == [{"id": str(kept), "name": "Kept"}]


async def test_a_playgroup_the_viewer_has_left_is_not_listed_while_another_still_is(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, viewer, headers = await make_user(app_schema)
    _, other, _ = await make_user(app_schema)
    kept, gone = await _group(app_schema, "Kept"), await _group(app_schema, "Gone")
    await join(app_schema, kept, viewer)
    await join(app_schema, gone, viewer, left=True)
    for group in (kept, gone):
        await join(app_schema, group, other)
    body = (await client.get(f"{URL}/{other}", headers=headers)).json()
    assert body["common_playgroups"] == [{"id": str(kept), "name": "Kept"}]


@pytest.mark.parametrize(
    ("viewer_role", "target_role"),
    [("owner", "member"), ("member", "owner"), ("admin", "member"), ("member", "admin")],
)
async def test_roles_make_no_difference_to_who_can_open_a_profile(
    client: httpx.AsyncClient, app_schema: AppSession, viewer_role: str, target_role: str
) -> None:
    _, viewer, headers = await make_user(app_schema)
    _, other, _ = await make_user(app_schema)
    group = await _group(app_schema, "Roles")
    await join(app_schema, group, viewer, viewer_role)
    await join(app_schema, group, other, target_role)
    response = await client.get(f"{URL}/{other}", headers=headers)
    assert response.status_code == 200
    assert response.json()["common_playgroups"] == [{"id": str(group), "name": "Roles"}]


async def test_viewing_your_own_profile_lists_all_your_active_playgroups(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, me, headers = await make_user(app_schema)
    one = await _group(app_schema, "B-two")
    two = await _group(app_schema, "A-one")
    left = await _group(app_schema, "C-left")
    await join(app_schema, one, me)
    await join(app_schema, two, me)
    await join(app_schema, left, me, left=True)
    response = await client.get(f"{URL}/{me}", headers=headers)
    assert response.status_code == 200
    assert response.json()["id"] == str(me)
    assert [g["name"] for g in response.json()["common_playgroups"]] == ["A-one", "B-two"]


# ---------------------------------------------------------------- criterion 2


async def test_a_stranger_404_has_the_same_shape_as_an_unknown_id_404(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, headers = await make_user(app_schema)
    _, stranger, _ = await make_user(app_schema)
    await join(app_schema, await _group(app_schema), stranger)
    hidden = await client.get(f"{URL}/{stranger}", headers=headers)
    missing = await client.get(f"{URL}/{uuid4()}", headers=headers)
    assert hidden.status_code == missing.status_code == 404
    assert _error(hidden) == _error(missing)
    assert _error(hidden)["code"] == "not_found"
    assert _header_names(hidden) == _header_names(missing)
    assert "location" not in hidden.headers
    assert hidden.headers["content-length"] == missing.headers["content-length"]
    assert str(stranger) not in hidden.text


async def test_a_non_uuid_id_is_the_same_404_as_an_unknown_id(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, headers = await make_user(app_schema)
    missing = await client.get(f"{URL}/{uuid4()}", headers=headers)
    for bad in ("not-a-uuid", "123", "%20", "0" * 40):
        garbage = await client.get(f"{URL}/{bad}", headers=headers)
        assert garbage.status_code == 404
        assert _error(garbage) == _error(missing)


async def test_a_former_member_of_the_only_shared_group_gets_404(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, viewer, headers = await make_user(app_schema)
    _, other, _ = await make_user(app_schema)
    group = await _group(app_schema)
    await join(app_schema, group, viewer, left=True)
    await join(app_schema, group, other)
    response = await client.get(f"{URL}/{other}", headers=headers)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_a_target_who_left_the_only_shared_group_gets_404(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, viewer, headers = await make_user(app_schema)
    _, other, _ = await make_user(app_schema)
    group = await _group(app_schema)
    await join(app_schema, group, viewer)
    await join(app_schema, group, other, left=True)
    assert (await client.get(f"{URL}/{other}", headers=headers)).status_code == 404


async def test_a_guest_of_a_playgroup_the_viewer_is_not_in_gets_404(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, viewer, headers = await make_user(app_schema)
    mine, theirs = await _group(app_schema, "Mine"), await _group(app_schema, "Theirs")
    await join(app_schema, mine, viewer)
    guest = await _guest(app_schema, home_playgroup_id=theirs)
    response = await client.get(f"{URL}/{guest}", headers=headers)
    assert response.status_code == 404
    assert "Theirs" not in response.text


async def test_a_guest_of_a_playgroup_the_viewer_has_left_gets_404(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, viewer, headers = await make_user(app_schema)
    group = await _group(app_schema)
    await join(app_schema, group, viewer, left=True)
    guest = await _guest(app_schema, home_playgroup_id=group)
    assert (await client.get(f"{URL}/{guest}", headers=headers)).status_code == 404


async def test_a_member_who_shares_nothing_is_never_a_403(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, headers = await make_user(app_schema)
    _, loner, _ = await make_user(app_schema)
    assert (await client.get(f"{URL}/{loner}", headers=headers)).status_code == 404


async def test_unknown_and_hidden_players_cost_the_same_number_of_statements(
    client: httpx.AsyncClient,
    app_schema: AppSession,
    count_statements: Callable[[], AbstractContextManager[StatementCounter]],
) -> None:
    _, _, headers = await make_user(app_schema)
    _, stranger, _ = await make_user(app_schema)
    await join(app_schema, await _group(app_schema), stranger)
    missing_id = uuid4()
    # Warm up so one-off work (such as caches) does not skew the first measurement.
    await client.get(f"{URL}/{stranger}", headers=headers)

    async def measure(target: UUID) -> int:
        with count_statements() as counter:
            assert (await client.get(f"{URL}/{target}", headers=headers)).status_code == 404
        return counter.count

    hidden = await measure(stranger)
    missing = await measure(missing_id)
    assert hidden == missing


# ---------------------------------------------------------------- criterion 3


async def test_a_one_hop_merge_redirects_to_the_survivor_and_the_location_serves_it(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, viewer, headers = await make_user(app_schema)
    _, survivor, _ = await make_user(app_schema)
    group = await _group(app_schema)
    await join(app_schema, group, viewer)
    await join(app_schema, group, survivor)
    old = await _guest(app_schema, merged_into_id=survivor)
    response = await client.get(f"{URL}/{old}", headers=headers, follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == f"{URL}/{survivor}"
    followed = await client.get(response.headers["location"], headers=headers)
    assert followed.status_code == 200
    assert followed.json()["id"] == str(survivor)


async def test_a_multi_hop_merge_redirects_straight_to_the_final_survivor(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, viewer, headers = await make_user(app_schema)
    _, survivor, _ = await make_user(app_schema)
    group = await _group(app_schema)
    await join(app_schema, group, viewer)
    await join(app_schema, group, survivor)
    tip = survivor
    chain = []
    for _ in range(4):
        tip = await _guest(app_schema, merged_into_id=tip)
        chain.append(tip)
    response = await client.get(f"{URL}/{chain[-1]}", headers=headers, follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == f"{URL}/{survivor}"
    assert response.json() == {"merged_into_id": str(survivor)}


async def test_the_redirect_body_carries_only_the_survivor_id(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, viewer, headers = await make_user(app_schema)
    _, survivor, _ = await make_user(app_schema)
    group = await _group(app_schema, "VisibleGroupName")
    await join(app_schema, group, viewer)
    await join(app_schema, group, survivor)
    await app_schema.execute(
        update(Player).where(Player.id == survivor).values(display_name="SurvivorSecretName")
    )
    old = await _guest(app_schema, merged_into_id=survivor)
    response = await client.get(f"{URL}/{old}", headers=headers, follow_redirects=False)
    assert response.status_code == 308
    assert response.json() == {"merged_into_id": str(survivor)}
    assert "SurvivorSecretName" not in response.text
    assert "VisibleGroupName" not in response.text


async def test_a_merged_guest_whose_survivor_is_hidden_is_the_same_404_as_a_stranger(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, headers = await make_user(app_schema)
    _, survivor, _ = await make_user(app_schema)
    await join(app_schema, await _group(app_schema), survivor)
    old = await _guest(app_schema, merged_into_id=survivor)
    merged = await client.get(f"{URL}/{old}", headers=headers, follow_redirects=False)
    stranger = await client.get(f"{URL}/{survivor}", headers=headers, follow_redirects=False)
    assert merged.status_code == stranger.status_code == 404
    assert _error(merged) == _error(stranger)
    assert "location" not in merged.headers
    assert str(survivor) not in merged.text


async def test_a_merged_guest_visible_by_home_group_but_survivor_hidden_is_404(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, viewer, headers = await make_user(app_schema)
    _, survivor, _ = await make_user(app_schema)
    home = await _group(app_schema)
    await join(app_schema, home, viewer)
    await join(app_schema, await _group(app_schema, "Elsewhere"), survivor)
    old = await _guest(app_schema, home_playgroup_id=home, merged_into_id=survivor)
    response = await client.get(f"{URL}/{old}", headers=headers, follow_redirects=False)
    assert response.status_code == 404
    assert "location" not in response.headers
    assert str(survivor) not in response.text


async def test_a_merge_loop_is_a_404_and_does_not_hang(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, viewer, headers = await make_user(app_schema)
    group = await _group(app_schema)
    await join(app_schema, group, viewer)
    a = await _guest(app_schema, home_playgroup_id=group)
    b = await _guest(app_schema, home_playgroup_id=group, merged_into_id=a)
    await app_schema.execute(update(Player).where(Player.id == a).values(merged_into_id=b))
    await app_schema.commit()
    for start in (a, b):
        response = await client.get(f"{URL}/{start}", headers=headers, follow_redirects=False)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


async def test_a_guest_merged_into_itself_is_a_404(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, viewer, headers = await make_user(app_schema)
    group = await _group(app_schema)
    await join(app_schema, group, viewer)
    a = await _guest(app_schema, home_playgroup_id=group)
    await app_schema.execute(update(Player).where(Player.id == a).values(merged_into_id=a))
    await app_schema.commit()
    response = await client.get(f"{URL}/{a}", headers=headers, follow_redirects=False)
    assert response.status_code == 404


async def test_a_dangling_merged_into_id_is_a_404(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, viewer, headers = await make_user(app_schema)
    group = await _group(app_schema)
    await join(app_schema, group, viewer)
    survivor = await _guest(app_schema, home_playgroup_id=group)
    old = await _guest(app_schema, home_playgroup_id=group, merged_into_id=survivor)
    # The FK normally prevents a dangling id; drop it for this connection's transaction only
    # when the database allows it, otherwise the row cannot exist and there is nothing to test.
    try:
        await app_schema.execute(text("SET session_replication_role = replica"))
        await app_schema.execute(
            update(Player).where(Player.id == old).values(merged_into_id=uuid4())
        )
        await app_schema.commit()
    except DBAPIError:
        await app_schema.rollback()
        pytest.skip("the database forbids a dangling merged_into_id")
    finally:
        await app_schema.execute(text("SET session_replication_role = origin"))
        await app_schema.commit()
    response = await client.get(f"{URL}/{old}", headers=headers, follow_redirects=False)
    assert response.status_code == 404
    assert "location" not in response.headers


async def test_a_merge_chain_longer_than_the_hop_limit_is_a_404(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, viewer, headers = await make_user(app_schema)
    _, survivor, _ = await make_user(app_schema)
    group = await _group(app_schema)
    await join(app_schema, group, viewer)
    await join(app_schema, group, survivor)
    tip = survivor
    for _ in range(HOP_LIMIT_PLUS):
        tip = await _guest(app_schema, merged_into_id=tip)
    response = await client.get(f"{URL}/{tip}", headers=headers, follow_redirects=False)
    assert response.status_code == 404
    assert "location" not in response.headers


async def test_the_redirect_location_is_a_path_this_api_serves(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, viewer, headers = await make_user(app_schema)
    _, survivor, _ = await make_user(app_schema)
    group = await _group(app_schema)
    await join(app_schema, group, viewer)
    await join(app_schema, group, survivor)
    old = await _guest(app_schema, merged_into_id=survivor)
    response = await client.get(f"{URL}/{old}", headers=headers, follow_redirects=False)
    location = response.headers["location"]
    assert location.startswith("/api/v1/players/")
    assert "://" not in location
    UUID(location.rsplit("/", 1)[-1])


async def test_a_merged_guest_viewed_by_its_survivor_redirects_to_their_own_profile(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, me, headers = await make_user(app_schema)
    old = await _guest(app_schema, merged_into_id=me)
    response = await client.get(f"{URL}/{old}", headers=headers, follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == f"{URL}/{me}"


# ------------------------------------------------- criterion 4 and privacy


async def test_the_member_profile_exposes_no_email_username_stats_or_internal_ids(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    other_user, other, _ = await make_user(app_schema)
    _, viewer, headers = await make_user(app_schema)
    group = await _group(app_schema)
    await join(app_schema, group, viewer)
    await join(app_schema, group, other)
    response = await client.get(f"{URL}/{other}", headers=headers)
    body = response.json()
    assert set(body) == KEYS
    for forbidden in (
        "email",
        "username",
        "user_id",
        "home_playgroup_id",
        "merged_into_id",
        "games_played",
        "win_rate",
        "wins",
        "stats",
    ):
        assert forbidden not in body
    assert str(other_user) not in response.text
    assert "@example.com" not in response.text
    assert "user" not in {k for k in body if k.startswith("user")}


async def test_the_guest_profile_exposes_no_home_playgroup_id_or_stats(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, viewer, headers = await make_user(app_schema)
    group = await _group(app_schema, "Home")
    await join(app_schema, group, viewer)
    guest = await _guest(app_schema, home_playgroup_id=group)
    body = (await client.get(f"{URL}/{guest}", headers=headers)).json()
    assert set(body) == KEYS
    for forbidden in ("home_playgroup_id", "user_id", "merged_into_id", "games_played", "win_rate"):
        assert forbidden not in body
    assert body["common_playgroups"][0].keys() == {"id", "name"}


# ---------------------------------------------------------------------- auth


async def test_the_profile_without_a_token_is_401(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, other, _ = await make_user(app_schema)
    response = await client.get(f"{URL}/{other}")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


async def test_an_expired_or_malformed_token_is_401(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    user_id, _, _ = await make_user(app_schema)
    _, other, _ = await make_user(app_schema)
    tokens = {
        "expired": issue(user_id, now=datetime.now(UTC) - timedelta(hours=1)),
        "expired_claims": encode(claims_for(user_id, ttl=-60)),
        "garbage": "not.a.jwt",
        "bad_signature": tamper_signature(issue(user_id)),
        "refresh_type": issue(user_id, "refresh"),
    }
    for name, token in tokens.items():
        response = await client.get(f"{URL}/{other}", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401, name


async def test_a_missing_token_wins_over_an_unknown_id(client: httpx.AsyncClient) -> None:
    assert (await client.get(f"{URL}/not-a-uuid")).status_code == 401


# ---------------------------------------------------------------- query count


async def test_the_200_uses_the_same_number_of_statements_for_one_and_ten_shared_groups(
    client: httpx.AsyncClient,
    app_schema: AppSession,
    count_statements: Callable[[], AbstractContextManager[StatementCounter]],
) -> None:
    _, viewer, headers = await make_user(app_schema)
    _, other, _ = await make_user(app_schema)

    async def share(n: int) -> None:
        for i in range(n):
            group = await _group(app_schema, f"Group {i:02d}")
            await join(app_schema, group, viewer)
            await join(app_schema, group, other)

    async def measure() -> tuple[int, int]:
        with count_statements() as counter:
            response = await client.get(f"{URL}/{other}", headers=headers)
        assert response.status_code == 200
        return counter.count, len(response.json()["common_playgroups"])

    await share(1)
    await measure()  # warm up
    one_count, one_len = await measure()
    await share(9)
    ten_count, ten_len = await measure()
    assert (one_len, ten_len) == (1, 10)
    assert one_count == ten_count


# ------------------------------------------------------------------- OpenAPI


async def test_openapi_lists_the_route_with_a_308_and_the_profile_schema(
    api_app: FastAPI,
) -> None:
    spec = api_app.openapi()
    operation = spec["paths"]["/api/v1/players/{player_id}"]["get"]
    assert set(operation["responses"]) >= {"200", "308"}
    ref = operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    schema = spec["components"]["schemas"][ref.rsplit("/", 1)[-1]]
    assert set(schema["properties"]) == KEYS
    assert operation["security"], "the route must advertise the bearer scheme"
