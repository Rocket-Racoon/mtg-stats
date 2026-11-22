"""The lobby, seat and turn handlers (CONTRACT.md section 4.4, S-6.1.6).

Pure: no clock, randomness or I/O. A handler runs after steps 1 to 7 of section 6.2 and returns
the first failure in this order (4.4.1): payload shape, phase, seat references, then its own rules.
A handler never mutates its input; it builds new lists and copies the state.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final, TypeGuard

from app.games.core.canonical import MAX_SAFE_INTEGER
from app.games.core.contract import (
    SETTING_RANGES,
    TRUST_MODES,
    Commander,
    EventEnvelope,
    GameState,
    Handler,
    ReducerError,
    ReducerErrorCode,
    Seat,
    Settings,
)

MAX_SEATS: Final = 8
_SETTING_NAMES: Final = (
    "starting_life",
    "commander_damage_threshold",
    "poison_threshold",
    "trust_mode",
)


def _reject(code: ReducerErrorCode) -> ReducerError:
    return ReducerError(code)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and abs(value) <= MAX_SAFE_INTEGER


def _is_seat_id(value: Any) -> TypeGuard[int]:
    return _is_int(value) and 1 <= value <= MAX_SEATS


def _is_id_or_null(value: Any) -> bool:
    """A player id, deck version id or card id: a non-empty string, or ``null`` where allowed."""
    return value is None or (isinstance(value, str) and value != "")


def _setting_is_valid(name: str, value: Any) -> bool:
    """Section 4.4.1, "Settings values": any other value, whatever its type, is invalid."""
    if name == "trust_mode":
        return isinstance(value, str) and value in TRUST_MODES
    low, high = SETTING_RANGES[name]
    return _is_int(value) and low <= value <= high


def _parse_commanders(value: Any) -> list[Commander] | None:
    """Section 4.4.1, "Commanders": 1 or 2 items with ``card_id`` and ``name``; else ``None``."""
    if not isinstance(value, list) or not 1 <= len(value) <= 2:
        return None
    parsed: list[Commander] = []
    for item in value:
        if not isinstance(item, dict) or "card_id" not in item or "name" not in item:
            return None
        card_id, name = item["card_id"], item["name"]
        if not _is_id_or_null(card_id) or not isinstance(name, str):
            return None
        parsed.append(Commander(card_id=card_id, name=name))
    return parsed


def _seat_ids(state: GameState) -> set[int]:
    return {seat.seat for seat in state.seats}


def _eliminated(seat_id: int | None, state: GameState) -> bool:
    return any(s.seat == seat_id and s.eliminated_at_seq is not None for s in state.seats)


def _replace_seat(state: GameState, seat_id: int, changes: dict[str, Any]) -> list[Seat]:
    return [
        seat.model_copy(update=changes) if seat.seat == seat_id else seat for seat in state.seats
    ]


def _game_created(state: GameState, event: EventEnvelope, seq: int) -> GameState | ReducerError:
    """Section 4.4.2: replace the settings, only as the first event of the log."""
    given = event.payload.get("settings")
    if not isinstance(given, dict) or any(name not in given for name in _SETTING_NAMES):
        return _reject("invalid_payload")
    if state.last_seq != 0:
        return _reject("invalid_phase")
    if not all(_setting_is_valid(name, given[name]) for name in _SETTING_NAMES):
        return _reject("invalid_setting")
    settings = Settings(**{name: given[name] for name in _SETTING_NAMES})
    return state.model_copy(update={"settings": settings, "last_seq": seq})


def _settings_changed(state: GameState, event: EventEnvelope, seq: int) -> GameState | ReducerError:
    """Section 4.4.3: a partial change of the settings, lobby only."""
    if state.phase != "lobby":
        return _reject("invalid_phase")
    present = {name: event.payload[name] for name in _SETTING_NAMES if name in event.payload}
    if not all(_setting_is_valid(name, value) for name, value in present.items()):
        return _reject("invalid_setting")
    values = {name: getattr(state.settings, name) for name in _SETTING_NAMES}
    values.update(present)
    update: dict[str, Any] = {"settings": Settings(**values), "last_seq": seq}
    if "starting_life" in present:
        life = present["starting_life"]
        update["seats"] = [seat.model_copy(update={"life": life}) for seat in state.seats]
    return state.model_copy(update=update)


def _seat_joined(state: GameState, event: EventEnvelope, seq: int) -> GameState | ReducerError:
    """Section 4.4.4: add a seat, appended to the end of ``order``."""
    payload = event.payload
    seat_id, player_id, name = payload.get("seat"), payload.get("player_id"), payload.get("name")
    if (
        not _is_seat_id(seat_id)
        or "player_id" not in payload
        or not _is_id_or_null(player_id)
        or "name" not in payload
        or not isinstance(name, str)
    ):
        return _reject("invalid_payload")
    if state.phase != "lobby":
        return _reject("invalid_phase")
    if len(state.seats) >= MAX_SEATS:
        return _reject("table_full")
    if seat_id in _seat_ids(state):
        return _reject("seat_taken")
    seat = Seat(
        seat=seat_id,
        player_id=player_id,
        name=name,
        deck_version_id=None,
        commanders=[Commander(card_id=None, name="")],
        life=state.settings.starting_life,
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
    seats = sorted([*state.seats, seat], key=lambda s: s.seat)
    return state.model_copy(
        update={"seats": seats, "order": [*state.order, seat.seat], "last_seq": seq}
    )


def _seat_left(state: GameState, event: EventEnvelope, seq: int) -> GameState | ReducerError:
    """Section 4.4.5: remove a seat from ``seats`` and ``order``, lobby only."""
    seat_id = event.payload.get("seat")
    if not _is_seat_id(seat_id):
        return _reject("invalid_payload")
    if state.phase != "lobby":
        return _reject("invalid_phase")
    if seat_id not in _seat_ids(state):
        return _reject("unknown_seat")
    return state.model_copy(
        update={
            "seats": [s for s in state.seats if s.seat != seat_id],
            "order": [s for s in state.order if s != seat_id],
            "last_seq": seq,
        }
    )


def _seat_moved(state: GameState, event: EventEnvelope, seq: int) -> GameState | ReducerError:
    """Section 4.4.6: exchange two positions in ``order``; a correction in an active game."""
    payload = event.payload
    source, target, correction = payload.get("from"), payload.get("to"), payload.get("correction")
    if not _is_seat_id(source) or not _is_seat_id(target):
        return _reject("invalid_payload")
    if correction is not None and not isinstance(correction, bool):
        return _reject("invalid_payload")
    if not (state.phase == "lobby" or (state.phase == "active" and correction is True)):
        return _reject("invalid_phase")
    present = _seat_ids(state)
    if source not in present or target not in present:
        return _reject("unknown_seat")
    order = list(state.order)
    i, j = order.index(source), order.index(target)
    order[i], order[j] = order[j], order[i]
    return state.model_copy(update={"order": order, "last_seq": seq})


def _commanders_change(
    state: GameState, event: EventEnvelope, seq: int, *, with_deck: bool
) -> GameState | ReducerError:
    payload = event.payload
    seat_id = payload.get("seat")
    commanders = _parse_commanders(payload.get("commanders"))
    deck = payload.get("deck_version_id")
    if (
        not _is_seat_id(seat_id)
        or commanders is None
        or (with_deck and (not isinstance(deck, str) or deck == ""))
    ):
        return _reject("invalid_payload")
    if state.phase != "lobby":
        return _reject("invalid_phase")
    if seat_id not in _seat_ids(state):
        return _reject("unknown_seat")
    changes: dict[str, Any] = {"commanders": commanders, "commander_cast": [0] * len(commanders)}
    if with_deck:
        changes["deck_version_id"] = deck
    return state.model_copy(
        update={"seats": _replace_seat(state, seat_id, changes), "last_seq": seq}
    )


def _deck_assigned(state: GameState, event: EventEnvelope, seq: int) -> GameState | ReducerError:
    """Section 4.4.7: set a seat's deck and replace its commanders."""
    return _commanders_change(state, event, seq, with_deck=True)


def _commanders_set(state: GameState, event: EventEnvelope, seq: int) -> GameState | ReducerError:
    """Section 4.4.8: replace a seat's commanders, keeping its deck."""
    return _commanders_change(state, event, seq, with_deck=False)


def _game_started(state: GameState, event: EventEnvelope, seq: int) -> GameState | ReducerError:
    """Section 4.4.9: leave the lobby; ``first_seat`` takes turn 1 of round 1."""
    first_seat, started_at = event.payload.get("first_seat"), event.payload.get("started_at")
    if not _is_seat_id(first_seat) or not isinstance(started_at, str):
        return _reject("invalid_payload")
    if state.phase != "lobby":
        return _reject("invalid_phase")
    if first_seat not in _seat_ids(state):
        return _reject("unknown_seat")
    if len(state.seats) < 2:
        return _reject("not_enough_players")
    life = state.settings.starting_life
    return state.model_copy(
        update={
            "phase": "active",
            "active_seat": first_seat,
            "first_seat": first_seat,
            "turn": 1,
            "round": 1,
            "started_at": started_at,
            "seats": [seat.model_copy(update={"life": life}) for seat in state.seats],
            "last_seq": seq,
        }
    )


def _first_alive_after(state: GameState, start: int | None) -> int | None:
    """Scan ``order`` cyclically after ``start`` (``start`` itself last) for a seat still in."""
    order = state.order
    position = order.index(start) if start in order else -1
    for candidate in [*order[position + 1 :], *order[: position + 1]]:
        if not _eliminated(candidate, state):
            return candidate
    return None


def _turn_passed(state: GameState, event: EventEnvelope, seq: int) -> GameState | ReducerError:
    """Section 4.4.10: next surviving seat; the round rises when play reaches the round head."""
    if state.phase != "active" or all(s.eliminated_at_seq is not None for s in state.seats):
        return _reject("invalid_phase")
    next_seat = _first_alive_after(state, state.active_seat)
    first = state.first_seat
    head = first if not _eliminated(first, state) else _first_alive_after(state, first)
    return state.model_copy(
        update={
            "active_seat": next_seat,
            "turn": state.turn + 1,
            "round": state.round + (1 if next_seat == head else 0),
            "last_seq": seq,
        }
    )


HANDLERS: Final[Mapping[str, Handler]] = {
    "game_created": _game_created,
    "settings_changed": _settings_changed,
    "seat_joined": _seat_joined,
    "seat_left": _seat_left,
    "seat_moved": _seat_moved,
    "deck_assigned": _deck_assigned,
    "commanders_set": _commanders_set,
    "game_started": _game_started,
    "turn_passed": _turn_passed,
}
"""The nine handlers of section 4.4, by event type."""
