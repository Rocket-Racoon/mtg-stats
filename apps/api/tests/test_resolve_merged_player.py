"""``identity.services.resolve_merged_player``: merge chains, loops and the hop limit."""

from uuid import UUID

import pytest

from app.core.db import AppSession
from app.core.ids import uuid7
from app.identity.services import resolve_merged_player
from app.identity.services.players import MAX_MERGE_HOPS
from tests.factories import GuestPlayerFactory, PlayerFactory, create

pytestmark = pytest.mark.usefixtures("app_schema")


async def _chain(session: AppSession, merges: int) -> tuple[UUID, UUID]:
    """A chain of ``merges`` merged guests ending at a survivor; returns (first id, survivor)."""
    survivor = await create(session, GuestPlayerFactory)
    head = survivor.id
    for _ in range(merges):
        head = (await create(session, GuestPlayerFactory, merged_into_id=head)).id
    return head, survivor.id


async def test_an_unmerged_player_resolves_to_itself(app_schema: AppSession) -> None:
    player = await create(app_schema, PlayerFactory, user_id=None)
    info = await resolve_merged_player(app_schema, player.id)
    assert info is not None
    assert info.id == player.id


async def test_a_chain_of_the_maximum_length_resolves_to_the_survivor(
    app_schema: AppSession,
) -> None:
    first, survivor = await _chain(app_schema, MAX_MERGE_HOPS)
    info = await resolve_merged_player(app_schema, first)
    assert info is not None
    assert info.id == survivor


async def test_a_chain_one_hop_too_long_resolves_to_none(app_schema: AppSession) -> None:
    first, _ = await _chain(app_schema, MAX_MERGE_HOPS + 1)
    assert await resolve_merged_player(app_schema, first) is None


async def test_a_self_merge_resolves_to_none(app_schema: AppSession) -> None:
    guest = await create(app_schema, GuestPlayerFactory)
    guest.merged_into_id = guest.id
    await app_schema.flush()
    assert await resolve_merged_player(app_schema, guest.id) is None


async def test_a_loop_resolves_to_none(app_schema: AppSession) -> None:
    a = await create(app_schema, GuestPlayerFactory)
    b = await create(app_schema, GuestPlayerFactory, merged_into_id=a.id)
    a.merged_into_id = b.id
    await app_schema.flush()
    assert await resolve_merged_player(app_schema, a.id) is None


async def test_an_unknown_id_resolves_to_none(app_schema: AppSession) -> None:
    assert await resolve_merged_player(app_schema, uuid7()) is None
