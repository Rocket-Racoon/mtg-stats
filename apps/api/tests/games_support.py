"""Helpers for the event-log tests (S-6.3.1): game rows, submissions and actors."""

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.games.core import Actor
from tests.factories import GameFactory, PlaygroupFactory, create

SYSTEM_ACTOR = Actor(kind="system", player_id=None)


def player_actor(player_id: UUID | None = None) -> Actor:
    """A ``player`` actor for a (made-up) player id."""
    return Actor(kind="player", player_id=str(player_id or uuid4()))


def submission(
    game_id: UUID | str,
    type: str = "settings_changed",
    payload: dict[str, Any] | None = None,
    *,
    event_id: str | None = None,
    on_behalf_of_seat: int | None = None,
) -> dict[str, Any]:
    """A client's optimistic envelope: no ``seq`` or ``ts``, a fresh UUID v4 ``event_id``."""
    return {
        "event_id": event_id or str(uuid4()),
        "game_id": str(game_id),
        "type": type,
        "payload": payload if payload is not None else {},
        "actor": {"kind": "player", "player_id": "client-claims-this"},
        "on_behalf_of_seat": on_behalf_of_seat,
        "v": 1,
    }


async def make_game(session: AsyncSession) -> UUID:
    """Persist a playgroup and an empty-lobby game in it (flushed, not committed)."""
    group = await create(session, PlaygroupFactory)
    game = await create(session, GameFactory, playgroup_id=group.id)
    return game.id
