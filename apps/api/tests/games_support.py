"""Helpers for the event-log tests (S-6.3.1): game rows, submissions and actors."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import uuid7
from app.games.core import Actor
from app.games.models import GameEvent
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


def lobby_and_turns(game_id: UUID | str, total: int) -> list[dict[str, Any]]:
    """``total`` envelopes (``seq`` 1..total) that the current reducer accepts, as stored.

    ``game_created``, four ``seat_joined``, two ``deck_assigned``, a ``settings_changed``,
    ``game_started`` and then ``turn_passed`` for the rest. Needs ``total >= 9``.
    """
    assert total >= 9
    base = [
        (
            "game_created",
            {
                "settings": {
                    "starting_life": 40,
                    "commander_damage_threshold": 21,
                    "poison_threshold": 10,
                    "trust_mode": "casual",
                }
            },
        ),
        *(
            ("seat_joined", {"seat": i, "player_id": f"p{i}", "name": f"Seat {i}"})
            for i in (1, 2, 3, 4)
        ),
        *(
            (
                "deck_assigned",
                {
                    "seat": i,
                    "deck_version_id": f"d{i}",
                    "commanders": [{"card_id": f"c{i}", "name": f"Commander {i}"}],
                },
            )
            for i in (1, 2)
        ),
        ("game_started", {"first_seat": 2, "started_at": "2026-11-21T12:00:00.000Z"}),
    ]
    types = [*base, *([("turn_passed", {})] * (total - len(base)))]
    stamp = "2026-11-21T12:00:00.000Z"
    actor = {"kind": "player", "player_id": "p1"}
    return [
        {
            "event_id": str(uuid4()),
            "game_id": str(game_id),
            "seq": seq,
            "type": type_,
            "payload": payload,
            "actor": actor,
            "on_behalf_of_seat": None,
            "ts": stamp,
            "v": 1,
        }
        for seq, (type_, payload) in enumerate(types, start=1)
    ]


async def insert_log(session: AsyncSession, game_id: UUID, envelopes: list[dict[str, Any]]) -> None:
    """Insert stored events in one statement (the log, not through ``append_event``).

    Leaves ``games.state`` and ``games.last_seq`` untouched, so the snapshot of a game made with
    ``GameFactory`` is stale on purpose.
    """
    rows = [
        {
            "id": uuid7(),
            "game_id": game_id,
            "seq": e["seq"],
            "event_id": UUID(e["event_id"]),
            "type": e["type"],
            "payload": e["payload"],
            "actor": e["actor"],
            "on_behalf_of_seat": e["on_behalf_of_seat"],
            "ts": datetime(2026, 11, 21, 12, 0, tzinfo=UTC),
            "v": e["v"],
        }
        for e in envelopes
    ]
    await session.execute(insert(GameEvent), rows)
    await session.flush()
