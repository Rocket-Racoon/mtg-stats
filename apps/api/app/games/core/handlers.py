"""The lobby, seat and turn handlers (S-6.1.6, CONTRACT.md 4.4), the life ones (S-6.1.3, 4.5),
commander damage (S-6.1.4, 4.6) and poison, counters, commander cast, monarch and initiative
(S-6.1.5, 4.7).

Pure: no clock, randomness or I/O. A handler runs after steps 1 to 7 of section 6.2 and returns
the first failure in this order (4.4.1, 4.5.1): payload shape, phase, seat references, then its own
rules. A handler never mutates its input; it builds new lists and copies the state.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final, TypeGuard, cast

from app.games.core.canonical import MAX_SAFE_INTEGER
from app.games.core.contract import (
    MAX_SEATS,
    MIN_SEATS,
    SETTING_RANGES,
    TRUST_MODES,
    WIN_CONDITION_CODES,
    Commander,
    EventEnvelope,
    GameState,
    Handler,
    ReducerError,
    ReducerErrorCode,
    Seat,
    Settings,
    WinCondition,
)
from app.games.core.elimination import Elimination, eliminate, next_standing_seat

MAX_LIFE_DELTA: Final = 999
"""The largest |delta| of a ``life_changed`` (4.5.3)."""

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
    # Only the keys present were checked: copy without validating, so settings that
    # initial_state stored unvalidated (section 3.5) never make a handler raise.
    update: dict[str, Any] = {
        "settings": state.settings.model_copy(update=present),
        "last_seq": seq,
    }
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
    # model_construct: life is copied from settings, which initial_state does not validate.
    seat = Seat.model_construct(
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
    if not (state.phase == "lobby" or (state.phase in ("active", "ending") and correction is True)):
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
    changes: dict[str, Any] = {
        "commanders": commanders,
        "commander_cast": [0] * len(commanders),
        # deck_assigned sets the deck; commanders_set plays without one (4.4.8).
        "deck_version_id": deck if with_deck else None,
    }
    return state.model_copy(
        update={"seats": _replace_seat(state, seat_id, changes), "last_seq": seq}
    )


def _deck_assigned(state: GameState, event: EventEnvelope, seq: int) -> GameState | ReducerError:
    """Section 4.4.7: set a seat's deck and replace its commanders."""
    return _commanders_change(state, event, seq, with_deck=True)


def _commanders_set(state: GameState, event: EventEnvelope, seq: int) -> GameState | ReducerError:
    """Section 4.4.8: replace a seat's commanders and clear its deck."""
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
    if len(state.seats) < MIN_SEATS:
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


def _turn_passed(state: GameState, event: EventEnvelope, seq: int) -> GameState | ReducerError:
    """Section 4.4.10: next surviving seat; the round rises when the pass walks over ``first_seat``.

    The pass walks the positions of ``order`` after the active seat, cyclically, up to and
    including the next seat's. The round goes up iff the position of ``first_seat`` is walked,
    whether or not that seat is eliminated. A null ``active_seat`` is not a legal state of an
    active or ending game; it is read as "before the first position", so the walk starts at the
    head of ``order`` and nothing raises.
    """
    if state.phase not in ("active", "ending") or all(
        s.eliminated_at_seq is not None for s in state.seats
    ):
        return _reject("invalid_phase")
    order = state.order
    next_seat = next_standing_seat(state, state.active_seat)
    start = order.index(state.active_seat) + 1 if state.active_seat in order else 0
    end = order.index(next_seat) if next_seat in order else start - 1
    # Number of positions walked: from start up to and including end, cyclically.
    walked = (end - start) % len(order) + 1 if order else 0
    walked_positions = {(start + k) % len(order) for k in range(walked)} if order else set()
    first = state.first_seat
    rises = first in order and order.index(first) in walked_positions
    return state.model_copy(
        update={
            "active_seat": next_seat,
            "turn": state.turn + 1,
            "round": state.round + (1 if rises else 0),
            "last_seq": seq,
        }
    )


def _is_running(state: GameState) -> bool:
    """Section 4.5.1: the three S-6.1.3 handlers are allowed in ``active`` and ``ending``."""
    return state.phase in ("active", "ending")


def _life_changed(state: GameState, event: EventEnvelope, seq: int) -> GameState | ReducerError:
    """Section 4.5.3: add ``delta`` to a seat's life, unclamped; eliminate it at 0 or less."""
    payload = event.payload
    seat_id: Any = payload.get("seat")
    delta: Any = payload.get("delta")
    source: Any = payload.get("source_seat")
    if not _is_seat_id(seat_id) or not _is_int(delta):
        return _reject("invalid_payload")
    if source is not None and not _is_seat_id(source):
        return _reject("invalid_payload")
    if not _is_running(state):
        return _reject("invalid_phase")
    present = _seat_ids(state)
    if seat_id not in present or (source is not None and source not in present):
        return _reject("unknown_seat")
    if _eliminated(seat_id, state) or (source is not None and _eliminated(source, state)):
        return _reject("seat_eliminated")
    if delta == 0 or abs(delta) > MAX_LIFE_DELTA:
        return _reject("invalid_amount")
    current: Any = next(s for s in state.seats if s.seat == seat_id).life
    # life may be unvalidated (3.5): only a safe integer qualifies, and the sum must stay safe.
    if not _is_int(current) or abs(current + delta) > MAX_SAFE_INTEGER:
        return _reject("invalid_amount")
    life = current + delta
    changed = state.model_copy(update={"seats": _replace_seat(state, seat_id, {"life": life})})
    if delta < 0 and life <= 0:
        changed = eliminate(changed, seq, [Elimination(seat_id, "life", source, None)])
    return changed.model_copy(update={"last_seq": seq})


def _player_conceded(state: GameState, event: EventEnvelope, seq: int) -> GameState | ReducerError:
    """Section 4.5.4: eliminate a seat with reason ``concession``, whatever its life."""
    seat_id = event.payload.get("seat")
    if not _is_seat_id(seat_id):
        return _reject("invalid_payload")
    if not _is_running(state):
        return _reject("invalid_phase")
    if seat_id not in _seat_ids(state):
        return _reject("unknown_seat")
    if _eliminated(seat_id, state):
        return _reject("seat_eliminated")
    changed = eliminate(state, seq, [Elimination(seat_id, "concession", None, None)])
    return changed.model_copy(update={"last_seq": seq})


def _player_eliminated(
    state: GameState, event: EventEnvelope, seq: int
) -> GameState | ReducerError:
    """Section 4.5.5: the host declares a seat out, with an optional D5 ``cause``."""
    seat_id, cause = event.payload.get("seat"), event.payload.get("cause")
    if not _is_seat_id(seat_id):
        return _reject("invalid_payload")
    if cause is not None and not (isinstance(cause, str) and cause in WIN_CONDITION_CODES):
        return _reject("invalid_payload")
    if not _is_running(state):
        return _reject("invalid_phase")
    if seat_id not in _seat_ids(state):
        return _reject("unknown_seat")
    if _eliminated(seat_id, state):
        return _reject("seat_eliminated")
    entry = Elimination(seat_id, "declared", None, cast(WinCondition | None, cause))
    changed = eliminate(state, seq, [entry])
    return changed.model_copy(update={"last_seq": seq})


def _commander_damage(state: GameState, event: EventEnvelope, seq: int) -> GameState | ReducerError:
    """Section 4.6: add commander damage to a tally and take the same amount off the target's life.

    Check order: payload shape, phase, ``unknown_seat``, ``seat_eliminated``, the amount (1 to
    999), the commander index of the *source* seat, then the numbers in the state. One entry at
    most goes to the elimination routine: ``commander_damage`` when the changed tally reaches the
    threshold, else ``life`` when the new life is 0 or less.
    """
    payload = event.payload
    source: Any = payload.get("source_seat")
    target: Any = payload.get("target_seat")
    commander: Any = payload.get("commander")
    amount: Any = payload.get("amount")
    if not (_is_seat_id(source) and _is_seat_id(target)):
        return _reject("invalid_payload")
    if not (_is_int(commander) and _is_int(amount)):
        return _reject("invalid_payload")
    if not _is_running(state):
        return _reject("invalid_phase")
    present = _seat_ids(state)
    if source not in present or target not in present:
        return _reject("unknown_seat")
    if _eliminated(target, state) or _eliminated(source, state):
        return _reject("seat_eliminated")
    if not 1 <= amount <= MAX_LIFE_DELTA:
        return _reject("invalid_amount")
    source_seat = next(s for s in state.seats if s.seat == source)
    if not 0 <= commander < len(source_seat.commanders):
        return _reject("unknown_commander")
    life: Any = next(s for s in state.seats if s.seat == target).life
    threshold: Any = state.settings.commander_damage_threshold
    if not _is_int(life) or not _is_int(threshold):
        return _reject("invalid_amount")
    row_key, key = str(target), f"{source}:{commander}"
    tally = state.commander_damage.get(row_key, {}).get(key, 0) + amount
    new_life = life - amount
    if abs(new_life) > MAX_SAFE_INTEGER or abs(tally) > MAX_SAFE_INTEGER:
        return _reject("invalid_amount")
    rows = {row: dict(tallies) for row, tallies in state.commander_damage.items()}
    rows.setdefault(row_key, {})[key] = tally
    changed = state.model_copy(
        update={
            "seats": _replace_seat(state, target, {"life": new_life}),
            "commander_damage": rows,
        }
    )
    if tally >= threshold:
        changed = eliminate(changed, seq, [Elimination(target, "commander_damage", source, None)])
    elif new_life <= 0:
        changed = eliminate(changed, seq, [Elimination(target, "life", source, None)])
    return changed.model_copy(update={"last_seq": seq})


def _seat_of(state: GameState, seat_id: int) -> Seat:
    return next(s for s in state.seats if s.seat == seat_id)


def _delta_ok(delta: int) -> bool:
    return delta != 0 and abs(delta) <= MAX_LIFE_DELTA


def _poison_changed(state: GameState, event: EventEnvelope, seq: int) -> GameState | ReducerError:
    """Section 4.7.1: add ``delta`` to a seat's poison.

    A positive delta that takes the poison to the threshold or more eliminates the seat with
    reason ``poison`` (``eliminated_by`` is ``source_seat``). The threshold is read on every
    event, removals included, so a non-integer one is ``invalid_amount`` either way. A negative
    delta never eliminates.
    """
    payload = event.payload
    seat_id: Any = payload.get("seat")
    delta: Any = payload.get("delta")
    source: Any = payload.get("source_seat")
    if not _is_seat_id(seat_id) or not _is_int(delta):
        return _reject("invalid_payload")
    if source is not None and not _is_seat_id(source):
        return _reject("invalid_payload")
    if not _is_running(state):
        return _reject("invalid_phase")
    present = _seat_ids(state)
    if seat_id not in present or (source is not None and source not in present):
        return _reject("unknown_seat")
    if _eliminated(seat_id, state) or (source is not None and _eliminated(source, state)):
        return _reject("seat_eliminated")
    if not _delta_ok(delta):
        return _reject("invalid_amount")
    current: Any = _seat_of(state, seat_id).poison
    threshold: Any = state.settings.poison_threshold
    if not _is_int(current) or not _is_int(threshold):
        return _reject("invalid_amount")
    if not 0 <= current + delta <= MAX_SAFE_INTEGER:
        return _reject("invalid_amount")
    poison = current + delta
    changed = state.model_copy(update={"seats": _replace_seat(state, seat_id, {"poison": poison})})
    if delta > 0 and poison >= threshold:
        changed = eliminate(changed, seq, [Elimination(seat_id, "poison", source, None)])
    return changed.model_copy(update={"last_seq": seq})


def _counter_changed(state: GameState, event: EventEnvelope, seq: int) -> GameState | ReducerError:
    """Section 4.7.2: add ``delta`` to a seat's energy or experience; never eliminates."""
    payload = event.payload
    seat_id: Any = payload.get("seat")
    delta: Any = payload.get("delta")
    counter: Any = payload.get("counter")
    if not _is_seat_id(seat_id) or not _is_int(delta):
        return _reject("invalid_payload")
    if not isinstance(counter, str) or counter not in ("energy", "experience"):
        return _reject("invalid_payload")
    if not _is_running(state):
        return _reject("invalid_phase")
    if seat_id not in _seat_ids(state):
        return _reject("unknown_seat")
    if _eliminated(seat_id, state):
        return _reject("seat_eliminated")
    if not _delta_ok(delta):
        return _reject("invalid_amount")
    current: Any = getattr(_seat_of(state, seat_id), counter)
    if not _is_int(current) or not 0 <= current + delta <= MAX_SAFE_INTEGER:
        return _reject("invalid_amount")
    return state.model_copy(
        update={
            "seats": _replace_seat(state, seat_id, {counter: current + delta}),
            "last_seq": seq,
        }
    )


def _commander_cast(state: GameState, event: EventEnvelope, seq: int) -> GameState | ReducerError:
    """Section 4.7.3: add 1 or -1 to the cast count of one of the seat's own commanders.

    The ``delta`` is checked before the commander index, then the stored count. The tax is
    derived (``2 * count``), never stored. The list is copied, never mutated.
    """
    payload = event.payload
    seat_id: Any = payload.get("seat")
    commander: Any = payload.get("commander")
    delta: Any = payload.get("delta")
    if not _is_seat_id(seat_id) or not _is_int(commander) or not _is_int(delta):
        return _reject("invalid_payload")
    if not _is_running(state):
        return _reject("invalid_phase")
    if seat_id not in _seat_ids(state):
        return _reject("unknown_seat")
    if _eliminated(seat_id, state):
        return _reject("seat_eliminated")
    if delta not in (1, -1):
        return _reject("invalid_amount")
    seat = _seat_of(state, seat_id)
    if not 0 <= commander < len(seat.commanders):
        return _reject("unknown_commander")
    counts: Any = seat.commander_cast
    current: Any = None
    if isinstance(counts, list) and commander < len(counts):
        current = counts[commander]
    if not _is_int(current) or not 0 <= current + delta <= MAX_SAFE_INTEGER:
        return _reject("invalid_amount")
    updated = list(counts)
    updated[commander] = current + delta
    return state.model_copy(
        update={
            "seats": _replace_seat(state, seat_id, {"commander_cast": updated}),
            "last_seq": seq,
        }
    )


def _holder_set(
    state: GameState, event: EventEnvelope, seq: int, key: str
) -> GameState | ReducerError:
    seat_id: Any = event.payload.get("seat")
    if not _is_seat_id(seat_id):
        return _reject("invalid_payload")
    if not _is_running(state):
        return _reject("invalid_phase")
    if seat_id not in _seat_ids(state):
        return _reject("unknown_seat")
    if _eliminated(seat_id, state):
        return _reject("seat_eliminated")
    return state.model_copy(update={key: seat_id, "last_seq": seq})


def _monarch_set(state: GameState, event: EventEnvelope, seq: int) -> GameState | ReducerError:
    """Section 4.7.4: ``seat`` becomes the one monarch; ``initiative`` is untouched."""
    return _holder_set(state, event, seq, "monarch")


def _initiative_set(state: GameState, event: EventEnvelope, seq: int) -> GameState | ReducerError:
    """Section 4.7.5: ``seat`` takes the initiative; ``monarch`` is untouched."""
    return _holder_set(state, event, seq, "initiative")


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
    "life_changed": _life_changed,
    "player_conceded": _player_conceded,
    "player_eliminated": _player_eliminated,
    "commander_damage": _commander_damage,
    "poison_changed": _poison_changed,
    "counter_changed": _counter_changed,
    "commander_cast": _commander_cast,
    "monarch_set": _monarch_set,
    "initiative_set": _initiative_set,
}
"""The eighteen handlers of sections 4.4 to 4.7, by event type."""
