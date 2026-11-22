"""Shared helpers for the S-6.1.6 tests: event builders, seated and active states, type lists.

``CARRIER_TYPE`` is the event type the older tests use when they only need "some valid event":
``turn_passed`` stopped being a no-op in S-6.1.6, so ``seat_claimed`` (still a no-op, valid in
every phase and state, unlike ``settings_changed``) took its place.
"""

from __future__ import annotations

import itertools
from typing import Any

from app.games.core import EVENT_TYPES, ApplyError, ApplyOk, GameState, apply, initial_state
from app.games.core.contract import Commander, Seat, Settings

GAME_ID = "018f0000-0000-7000-8000-000000000001"
PLAYER_ID = "018f0000-0000-7000-8000-0000000000a1"

CARRIER_TYPE = "seat_claimed"
S_6_1_6_TYPES: tuple[str, ...] = (
    "game_created",
    "settings_changed",
    "seat_joined",
    "seat_left",
    "seat_moved",
    "deck_assigned",
    "commanders_set",
    "game_started",
    "turn_passed",
)
S_6_1_3_TYPES: tuple[str, ...] = ("life_changed", "player_conceded", "player_eliminated")
S_6_1_4_TYPES: tuple[str, ...] = ("commander_damage",)
S_6_1_5_TYPES: tuple[str, ...] = (
    "poison_changed",
    "counter_changed",
    "commander_cast",
    "monarch_set",
    "initiative_set",
)
NO_OP_TYPES: tuple[str, ...] = tuple(
    t
    for t in EVENT_TYPES
    if t not in S_6_1_6_TYPES
    and t not in S_6_1_3_TYPES
    and t not in S_6_1_4_TYPES
    and t not in S_6_1_5_TYPES
)

DEFAULT_SETTINGS = Settings(
    starting_life=40, commander_damage_threshold=21, poison_threshold=10, trust_mode="casual"
)
_counter = itertools.count(1)


def make_event(
    event_type: str, payload: dict[str, Any] | None = None, **extra: Any
) -> dict[str, Any]:
    """A valid envelope of ``event_type`` with a fresh ``event_id``; ``extra`` overrides keys."""
    event: dict[str, Any] = {
        "event_id": f"00000000-0000-4000-8000-{next(_counter):012d}",
        "game_id": GAME_ID,
        "type": event_type,
        "payload": {} if payload is None else payload,
        "actor": {"kind": "player", "player_id": PLAYER_ID},
        "on_behalf_of_seat": None,
        "v": 1,
    }
    event.update(extra)
    return event


def reject_code(state: GameState, event: Any) -> str:
    """The error code ``apply`` returns for ``event``; fails when the event is accepted."""
    result = apply(state, event)
    assert isinstance(result, ApplyError), f"expected a rejection, got {result}"
    return str(result.error.code)


def accept(state: GameState, event: Any) -> GameState:
    """The state ``apply`` returns for ``event``; fails when the event is rejected."""
    result = apply(state, event)
    assert isinstance(result, ApplyOk), f"expected ok, got {result}"
    return result.state


def new_seat(seat_id: int, *, life: int = 40, **updates: Any) -> Seat:
    """A seat as ``seat_joined`` creates it (4.4.1), with ``updates`` applied on top."""
    seat = Seat(
        seat=seat_id,
        player_id=f"p{seat_id}",
        name=f"Seat {seat_id}",
        deck_version_id=None,
        commanders=[Commander(card_id=None, name="")],
        life=life,
        poison=0,
        energy=0,
        experience=0,
        commander_cast=[0],
        elimination_reason=None,
        eliminated_by=None,
        eliminated_at_seq=None,
        eliminated_round=None,
        elimination_cause=None,
    )
    return seat.model_copy(update=updates) if updates else seat


def eliminated(seat_id: int, *, at_seq: int = 5, round_: int = 1) -> Seat:
    """A seat that satisfies the elimination invariant (3.3), eliminated by life loss."""
    return new_seat(
        seat_id,
        life=0,
        elimination_reason="life",
        eliminated_at_seq=at_seq,
        eliminated_round=round_,
    )


def lobby() -> GameState:
    """The empty lobby (3.5) with the default settings."""
    return initial_state(GAME_ID, DEFAULT_SETTINGS)


def seated(count: int) -> GameState:
    """A lobby with seats 1..count in order, built through the model (not through events)."""
    seats = [new_seat(i) for i in range(1, count + 1)]
    return lobby().model_copy(update={"seats": seats, "order": [s.seat for s in seats]})


def active_game(
    order: list[int],
    first: int,
    active: int,
    *,
    turn: int = 1,
    round_: int = 1,
    out: tuple[int, ...] = (),
) -> GameState:
    """An active game built through the models; seats in ``out`` are eliminated."""
    seats = sorted(
        (eliminated(i) if i in out else new_seat(i) for i in order), key=lambda s: s.seat
    )
    return lobby().model_copy(
        update={
            "phase": "active",
            "seats": seats,
            "order": list(order),
            "first_seat": first,
            "active_seat": active,
            "turn": turn,
            "round": round_,
            "started_at": "2026-11-21T12:00:00.000Z",
            "last_seq": 10,
        }
    )
