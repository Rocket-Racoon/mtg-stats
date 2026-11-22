"""Unit tests of the S-6.1.5 handlers (CONTRACT.md 4.7), twin of ``counters.test.ts``.

``poison_changed``, ``counter_changed``, ``commander_cast``, ``monarch_set`` and
``initiative_set``. The shared vectors pin everything reachable through events. This file adds what
a vector cannot say: hand-built states (a poison above the threshold, unvalidated numbers, the safe
range), the ``ending`` and the closed phases, the check order and purity of the input. The
acceptance tests of the story are written separately, from its criteria.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.games.core import ApplyOk, GameState, apply, canonical_json
from app.games.core.contract import Commander, Seat
from app.games.tests.lobby_helpers import accept, active_game, make_event

PHASES = ("lobby", "active", "ending", "finished", "abandoned")
OTHER_PHASES = ("lobby", "finished", "abandoned")
MAX = 2**53 - 1


def _game(count: int = 4) -> GameState:
    return active_game(list(range(1, count + 1)), 1, 1)


def _poison(seat: Any, delta: Any, source: Any = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"seat": seat, "delta": delta}
    if source is not None:
        payload["source_seat"] = source
    return make_event("poison_changed", payload)


def _counter(seat: Any, name: Any, delta: Any) -> dict[str, Any]:
    return make_event("counter_changed", {"seat": seat, "counter": name, "delta": delta})


def _cast(seat: Any, commander: Any, delta: Any) -> dict[str, Any]:
    return make_event("commander_cast", {"seat": seat, "commander": commander, "delta": delta})


def _in_phase(state: GameState, phase: str) -> GameState:
    return state.model_copy(
        update={"phase": phase, "resume_phase": "active" if phase == "abandoned" else None}
    )


def _seat(state: GameState, seat_id: int) -> Seat:
    return next(s for s in state.seats if s.seat == seat_id)


def _with_seat(state: GameState, seat_id: int, **changes: Any) -> GameState:
    """Change a seat without validation (``model_copy`` does not validate)."""
    seats = [s.model_copy(update=changes) if s.seat == seat_id else s for s in state.seats]
    return state.model_copy(update={"seats": seats})


def _with_settings(state: GameState, **changes: Any) -> GameState:
    return state.model_copy(update={"settings": state.settings.model_copy(update=changes)})


def _partners(state: GameState, seat_id: int) -> GameState:
    return _with_seat(
        state,
        seat_id,
        commanders=[Commander(card_id=None, name="A"), Commander(card_id=None, name="B")],
        commander_cast=[0, 0],
    )


def _run(state: GameState, *events: dict[str, Any]) -> GameState:
    for event in events:
        state = accept(state, event)
    return state


def _code(state: GameState, event: Any) -> str:
    result = apply(state, event)
    return "ok" if isinstance(result, ApplyOk) else str(result.error.code)


def _canon(state: GameState) -> str:
    return canonical_json(state.to_json_value())


def _conceded(state: GameState, seat_id: int) -> GameState:
    return accept(state, make_event("player_conceded", {"seat": seat_id}))


# --- poison_changed (4.7.1) ---


def test_criterion_1_seat_2_at_9_with_the_default_threshold_plus_1_from_seat_4_is_eliminated() -> (
    None
):
    before = _with_seat(_game(), 2, poison=9)
    seat = _seat(accept(before, _poison(2, 1, 4)), 2)
    assert (seat.poison, seat.elimination_reason, seat.eliminated_by) == (10, "poison", 4)
    assert (seat.eliminated_at_seq, seat.eliminated_round) == (before.last_seq + 1, 1)
    assert (seat.elimination_cause, seat.life) == (None, 40)


def test_criterion_1_with_a_threshold_of_15_the_same_seat_is_eliminated_only_at_15() -> None:
    before = _with_settings(_with_seat(_game(), 2, poison=9), poison_threshold=15)
    at14 = accept(before, _poison(2, 5, 4))
    assert (_seat(at14, 2).poison, _seat(at14, 2).eliminated_at_seq) == (14, None)
    at15 = accept(at14, _poison(2, 1, 4))
    assert (_seat(at15, 2).poison, _seat(at15, 2).elimination_reason) == (15, "poison")


def test_a_negative_delta_never_eliminates_even_above_the_threshold() -> None:
    state = accept(_with_seat(_game(), 2, poison=12), _poison(2, -1, 3))
    assert (_seat(state, 2).poison, _seat(state, 2).eliminated_at_seq) == (11, None)
    assert state.no_winner is False


def test_a_positive_delta_on_a_seat_already_above_the_threshold_eliminates_it() -> None:
    seat = _seat(accept(_with_seat(_game(), 2, poison=12), _poison(2, 1)), 2)
    assert (seat.poison, seat.elimination_reason, seat.eliminated_by) == (13, "poison", None)


def test_poison_is_not_clamped_to_the_threshold() -> None:
    seat = _seat(accept(_game(), _poison(2, 12)), 2)
    assert (seat.poison, seat.elimination_reason) == (12, "poison")


def test_self_inflicted_poison_names_the_seat_as_eliminator() -> None:
    seat = _seat(accept(_game(), _poison(3, 10, 3)), 3)
    assert (seat.poison, seat.eliminated_by) == (10, 3)


def test_an_integer_threshold_outside_1_to_99_is_used_as_given() -> None:
    zero = accept(_with_settings(_game(), poison_threshold=0), _poison(2, 1))
    assert _seat(zero, 2).elimination_reason == "poison"
    big = accept(_with_settings(_game(), poison_threshold=1000), _poison(2, 999))
    assert (_seat(big, 2).poison, _seat(big, 2).eliminated_at_seq) == (999, None)
    negative = accept(_with_settings(_game(), poison_threshold=-5), _poison(2, 1))
    assert _seat(negative, 2).elimination_reason == "poison"


@pytest.mark.parametrize(
    "build",
    [
        lambda s: _with_settings(s, poison_threshold="10"),
        lambda s: _with_settings(s, poison_threshold=None),
        lambda s: _with_settings(s, poison_threshold=1.5),
        lambda s: _with_settings(s, poison_threshold=10.0),
        lambda s: _with_settings(s, poison_threshold=True),
        lambda s: _with_settings(s, poison_threshold=MAX + 1),
        lambda s: _with_seat(s, 2, poison="3"),
        lambda s: _with_seat(s, 2, poison=None),
        lambda s: _with_seat(s, 2, poison=0.5),
        lambda s: _with_seat(s, 2, poison=3.0),
        lambda s: _with_seat(s, 2, poison=False),
        lambda s: _with_seat(s, 2, poison=-4),
        lambda s: _with_seat(s, 2, poison=[]),
    ],
)
def test_a_stored_number_that_is_not_an_integer_is_invalid_amount_for_both_signs(
    build: Any,
) -> None:
    state = build(_game())
    for delta in (1, -1):
        assert _code(state, _poison(2, delta)) == "invalid_amount"


def test_a_new_poison_above_the_safe_range_is_invalid_amount_and_the_edge_is_allowed() -> None:
    top = _with_settings(_with_seat(_game(), 2, poison=MAX), poison_threshold=MAX)
    assert _code(top, _poison(2, 1)) == "invalid_amount"
    assert _code(top, _poison(2, -1)) == "ok"
    edge = _with_settings(_with_seat(_game(), 2, poison=MAX - 1), poison_threshold=MAX)
    seat = _seat(accept(edge, _poison(2, 1)), 2)
    assert (seat.poison, seat.elimination_reason) == (MAX, "poison")


def test_poison_changed_in_the_ending_phase_and_invalid_phase_elsewhere() -> None:
    assert _code(_in_phase(_game(), "ending"), _poison(2, 1)) == "ok"
    for phase in OTHER_PHASES:
        assert _code(_in_phase(_game(), phase), _poison(2, 1)) == "invalid_phase"


def test_poison_elimination_ignores_the_life_of_the_seat() -> None:
    seat = _seat(accept(_with_seat(_game(), 2, life=-3), _poison(2, 10, 1)), 2)
    assert (seat.elimination_reason, seat.life) == ("poison", -3)


@pytest.mark.parametrize("delta", [0, 1000, -1000])
def test_a_delta_of_zero_or_beyond_999_is_invalid_amount(delta: int) -> None:
    assert _code(_game(), _poison(2, delta)) == "invalid_amount"


@pytest.mark.parametrize("delta", ["1", True, None, 1.5, 1.0, MAX + 1, [], {}])
def test_a_delta_that_is_not_an_integer_is_invalid_payload(delta: Any) -> None:
    assert _code(_game(), _poison(2, delta)) == "invalid_payload"
    assert _code(_game(), make_event("poison_changed", {"seat": 2})) == "invalid_payload"


@pytest.mark.parametrize("bad", [True, "1", 0, 9, 1.5, None])
def test_a_bad_seat_or_source_seat_is_invalid_payload(bad: Any) -> None:
    assert _code(_game(), _poison(bad, 1)) == "invalid_payload"
    assert _code(
        _game(), make_event("poison_changed", {"seat": 2, "delta": 1, "source_seat": bad})
    ) == ("ok" if bad is None else "invalid_payload")


def test_a_source_seat_of_null_means_no_source() -> None:
    state = accept(
        _with_seat(_game(), 2, poison=9),
        make_event("poison_changed", {"seat": 2, "delta": 1, "source_seat": None}),
    )
    assert _seat(state, 2).eliminated_by is None


def test_the_check_order_of_poison_changed() -> None:
    live = _game()
    gone = _conceded(live, 2)
    lobby = _in_phase(live, "lobby")
    assert _code(lobby, _poison(2, "1")) == "invalid_payload"
    assert _code(lobby, _poison(7, 0)) == "invalid_phase"
    assert _code(gone, _poison(7, 0)) == "unknown_seat"
    assert _code(gone, _poison(2, 0)) == "seat_eliminated"
    assert _code(gone, _poison(1, 0, 2)) == "seat_eliminated"
    assert _code(gone, _poison(1, -1, 2)) == "seat_eliminated"
    assert _code(live, _poison(2, 0)) == "invalid_amount"
    assert _code(live, make_event("poison_changed", {"seat": 2, "delta": 1, "source_seat": 7})) == (
        "unknown_seat"
    )


def test_the_poison_elimination_hands_the_crown_off() -> None:
    state = _run(
        _with_seat(_game(), 3, poison=9),
        make_event("monarch_set", {"seat": 3}),
        _poison(3, 1, 2),
    )
    assert (state.monarch, state.initiative) == (1, None)


def test_a_rejected_poison_event_changes_nothing_and_the_input_is_not_mutated() -> None:
    before = _with_seat(_game(), 2, poison=9)
    snapshot = _canon(before)
    assert _code(before, _poison(2, 0)) == "invalid_amount"
    accept(before, _poison(2, 1, 4))
    assert _canon(before) == snapshot


def test_the_seq_of_the_event_is_the_eliminated_at_seq() -> None:
    before = _with_seat(_game(), 2, poison=9)
    state = accept(before, make_event("poison_changed", {"seat": 2, "delta": 1}, seq=11))
    assert (state.last_seq, _seat(state, 2).eliminated_at_seq) == (11, 11)


# --- counter_changed (4.7.2) ---


@pytest.mark.parametrize(
    "bad", ["poison", "Energy", "EXPERIENCE", "", " energy", "life", None, 1, True, []]
)
def test_the_counter_is_exactly_energy_or_experience(bad: Any) -> None:
    assert _code(_game(), _counter(2, bad, 1)) == "invalid_payload"


def test_a_missing_counter_is_invalid_payload() -> None:
    event = make_event("counter_changed", {"seat": 2, "delta": 1})
    assert _code(_game(), event) == "invalid_payload"


@pytest.mark.parametrize("name", ["energy", "experience"])
def test_a_counter_change_touches_only_that_counter_of_that_seat(name: str) -> None:
    state = accept(_game(), _counter(3, name, 4))
    other = "experience" if name == "energy" else "energy"
    seat = _seat(state, 3)
    assert (getattr(seat, name), getattr(seat, other), seat.poison) == (4, 0, 0)
    assert getattr(_seat(state, 2), name) == 0


@pytest.mark.parametrize("name", ["energy", "experience"])
def test_a_counter_at_the_safe_range_limit(name: str) -> None:
    top = _with_seat(_game(), 2, **{name: MAX})
    assert _code(top, _counter(2, name, 1)) == "invalid_amount"
    assert getattr(_seat(accept(top, _counter(2, name, -1)), 2), name) == MAX - 1
    edge = _with_seat(_game(), 2, **{name: MAX - 999})
    assert getattr(_seat(accept(edge, _counter(2, name, 999)), 2), name) == MAX


@pytest.mark.parametrize("bad", ["3", 2.5, 3.0, None, True, -4, []])
def test_a_counter_value_that_is_not_a_non_negative_integer_is_invalid_amount(bad: Any) -> None:
    state = _with_seat(_game(), 2, energy=bad)
    assert _code(state, _counter(2, "energy", 1)) == "invalid_amount"
    assert _code(state, _counter(2, "energy", -1)) == "invalid_amount"


def test_a_counter_cannot_go_below_zero() -> None:
    state = _with_seat(_game(), 2, energy=1)
    assert _code(state, _counter(2, "energy", -2)) == "invalid_amount"
    assert _seat(accept(state, _counter(2, "energy", -1)), 2).energy == 0


@pytest.mark.parametrize("delta", [0, 1000, -1000])
def test_a_counter_delta_of_zero_or_beyond_999_is_invalid_amount(delta: int) -> None:
    assert _code(_game(), _counter(2, "energy", delta)) == "invalid_amount"


@pytest.mark.parametrize("delta", ["1", True, None, 1.5, [], {}])
def test_a_counter_delta_that_is_not_an_integer_is_invalid_payload(delta: Any) -> None:
    assert _code(_game(), _counter(2, "energy", delta)) == "invalid_payload"


def test_a_counter_never_eliminates_and_never_touches_the_table() -> None:
    state = _run(_game(), _counter(1, "energy", 999), _counter(1, "experience", 999))
    assert all(s.eliminated_at_seq is None for s in state.seats)
    assert (state.active_seat, state.turn, state.round, state.order) == (1, 1, 1, [1, 2, 3, 4])


def test_counter_changed_in_the_ending_phase_and_invalid_phase_elsewhere() -> None:
    assert _code(_in_phase(_game(), "ending"), _counter(2, "energy", 1)) == "ok"
    for phase in OTHER_PHASES:
        assert _code(_in_phase(_game(), phase), _counter(2, "energy", 1)) == "invalid_phase"


def test_the_check_order_of_counter_changed() -> None:
    live = _game()
    gone = _conceded(live, 2)
    assert _code(_in_phase(live, "lobby"), _counter(2, "poison", 1)) == "invalid_payload"
    assert _code(_in_phase(live, "lobby"), _counter(2, "energy", 0)) == "invalid_phase"
    assert _code(gone, _counter(7, "energy", 0)) == "unknown_seat"
    assert _code(gone, _counter(2, "energy", 0)) == "seat_eliminated"
    assert _code(live, _counter(2, "energy", 0)) == "invalid_amount"


def test_a_rejected_counter_event_changes_nothing() -> None:
    before = _with_seat(_game(), 2, energy=1)
    snapshot = _canon(before)
    assert _code(before, _counter(2, "energy", -2)) == "invalid_amount"
    assert _canon(before) == snapshot


# --- commander_cast (4.7.3) ---


def test_criterion_2_partner_b_cast_twice_is_2_tax_4_and_a_stays_0() -> None:
    state = _run(_partners(_game(), 2), _cast(2, 1, 1), _cast(2, 1, 1))
    assert _seat(state, 2).commander_cast == [0, 2]
    assert 2 * _seat(state, 2).commander_cast[1] == 4
    assert "tax" not in _canon(state)


def test_a_cast_goes_down_with_minus_1_to_0_and_not_below() -> None:
    state = _with_seat(_partners(_game(), 2), 2, commander_cast=[0, 2])
    state = _run(state, _cast(2, 1, -1), _cast(2, 1, -1))
    assert _seat(state, 2).commander_cast == [0, 0]
    assert _code(state, _cast(2, 1, -1)) == "invalid_amount"


@pytest.mark.parametrize("delta", [0, 2, -2, 3, 999, -999, 1000])
def test_only_plus_1_and_minus_1_are_allowed(delta: int) -> None:
    state = _run(_game(), _cast(2, 0, 1), _cast(2, 0, 1), _cast(2, 0, 1))
    assert _code(state, _cast(2, 0, delta)) == "invalid_amount"
    assert _code(state, _cast(2, 0, -1)) == "ok"


def test_the_count_is_per_commander() -> None:
    state = accept(_partners(_game(), 2), _cast(2, 0, 1))
    assert _code(state, _cast(2, 1, -1)) == "invalid_amount"
    assert _code(state, _cast(2, 0, -1)) == "ok"


def test_unknown_commander_is_checked_against_the_seat_itself_after_the_delta() -> None:
    state = _partners(_game(), 2)
    assert _code(state, _cast(2, 2, 1)) == "unknown_commander"
    assert _code(state, _cast(3, 1, 1)) == "unknown_commander"
    assert _code(state, _cast(2, -1, 1)) == "unknown_commander"
    assert _code(state, _cast(2, 5, 2)) == "invalid_amount"
    assert _code(state, _cast(3, 1, -1)) == "unknown_commander"


@pytest.mark.parametrize("counts", [["0", 0], [0.5, 0], [None, 0], [-5, 0], [True, 0], []])
def test_a_cast_count_that_is_not_a_non_negative_integer_is_invalid_amount(
    counts: list[Any],
) -> None:
    state = _with_seat(_partners(_game(), 2), 2, commander_cast=counts)
    assert _code(state, _cast(2, 0, 1)) == "invalid_amount"
    assert _code(state, _cast(2, 0, -1)) == "invalid_amount"


def test_a_commander_cast_that_is_not_a_list_is_invalid_amount_and_never_raises() -> None:
    bad_values: list[Any] = [None, "0", 0, {}]
    for bad in bad_values:
        state = _with_seat(_game(), 2, commander_cast=bad)
        assert _code(state, _cast(2, 0, 1)) == "invalid_amount"


def test_a_count_at_the_safe_range_limit() -> None:
    top = _with_seat(_game(), 2, commander_cast=[MAX])
    assert _code(top, _cast(2, 0, 1)) == "invalid_amount"
    assert _seat(accept(top, _cast(2, 0, -1)), 2).commander_cast == [MAX - 1]


@pytest.mark.parametrize("commander", ["0", True, None, 0.0, 1.5, [], MAX + 1])
def test_a_commander_that_is_not_an_integer_is_invalid_payload(commander: Any) -> None:
    assert _code(_game(), _cast(2, commander, 1)) == "invalid_payload"


@pytest.mark.parametrize("delta", ["1", True, None, 1.0, [], {}])
def test_a_cast_delta_that_is_not_an_integer_is_invalid_payload(delta: Any) -> None:
    assert _code(_game(), _cast(2, 0, delta)) == "invalid_payload"
    assert _code(_game(), make_event("commander_cast", {"seat": 2, "commander": 0})) == (
        "invalid_payload"
    )


def test_commander_cast_in_the_ending_phase_and_invalid_phase_elsewhere() -> None:
    assert _code(_in_phase(_game(), "ending"), _cast(2, 0, 1)) == "ok"
    for phase in OTHER_PHASES:
        assert _code(_in_phase(_game(), phase), _cast(2, 0, 1)) == "invalid_phase"


def test_the_check_order_of_commander_cast() -> None:
    live = _game()
    gone = _conceded(live, 2)
    assert _code(_in_phase(live, "lobby"), _cast(2, 0, "x")) == "invalid_payload"
    assert _code(_in_phase(live, "lobby"), _cast(2, 0, 2)) == "invalid_phase"
    assert _code(gone, _cast(7, 9, 2)) == "unknown_seat"
    assert _code(gone, _cast(2, 9, 0)) == "seat_eliminated"
    assert _code(live, _cast(2, 9, 2)) == "invalid_amount"
    assert _code(live, _cast(2, 9, 1)) == "unknown_commander"
    assert _code(live, _cast(2, 0, -1)) == "invalid_amount"


def test_commander_cast_does_not_mutate_the_input_list() -> None:
    before = _game()
    original = _seat(before, 2).commander_cast
    after = accept(before, _cast(2, 0, 1))
    assert original == [0]
    assert _seat(before, 2).commander_cast is original
    assert _seat(after, 2).commander_cast is not original
    assert _seat(after, 2).commander_cast == [1]


def test_commander_cast_changes_only_that_entry_and_no_elimination_or_damage() -> None:
    before = _partners(_game(), 2)
    after = accept(before, _cast(2, 1, 1))
    assert after.model_copy(update={"seats": [], "last_seq": 0}) == before.model_copy(
        update={"seats": [], "last_seq": 0}
    )
    assert _seat(after, 3) == _seat(before, 3)
    assert all(s.eliminated_at_seq is None for s in after.seats)


# --- monarch_set and initiative_set (4.7.4, 4.7.5) ---

SETS = [("monarch_set", "monarch", "initiative"), ("initiative_set", "initiative", "monarch")]


@pytest.mark.parametrize(("event_type", "key", "other"), SETS)
def test_only_one_seat_holds_it_and_the_other_key_is_untouched(
    event_type: str, key: str, other: str
) -> None:
    base = _game().model_copy(update={other: 4})
    state = _run(base, make_event(event_type, {"seat": 1}), make_event(event_type, {"seat": 3}))
    assert (getattr(state, key), getattr(state, other)) == (3, 4)


@pytest.mark.parametrize(("event_type", "key", "other"), SETS)
def test_naming_the_current_holder_again_changes_only_last_seq(
    event_type: str, key: str, other: str
) -> None:
    state = accept(_game(), make_event(event_type, {"seat": 2}))
    again = accept(state, make_event(event_type, {"seat": 2}))
    assert _canon(again) == _canon(state.model_copy(update={"last_seq": state.last_seq + 1}))
    assert getattr(again, key) == 2


@pytest.mark.parametrize(("event_type", "key", "other"), SETS)
def test_one_seat_can_hold_both(event_type: str, key: str, other: str) -> None:
    state = _run(
        _game(), make_event("monarch_set", {"seat": 2}), make_event("initiative_set", {"seat": 2})
    )
    assert (state.monarch, state.initiative) == (2, 2)


@pytest.mark.parametrize(("event_type", "key", "other"), SETS)
@pytest.mark.parametrize("bad", [None, True, "2", 0, 9, 1.5, 2.0])
def test_a_seat_that_is_not_a_seat_id_cannot_clear_or_set_it(
    event_type: str, key: str, other: str, bad: Any
) -> None:
    state = accept(_game(), make_event(event_type, {"seat": 2}))
    assert _code(state, make_event(event_type, {"seat": bad})) == "invalid_payload"
    assert _code(state, make_event(event_type, {})) == "invalid_payload"


@pytest.mark.parametrize(("event_type", "key", "other"), SETS)
def test_unknown_and_eliminated_seats_are_rejected_in_order(
    event_type: str, key: str, other: str
) -> None:
    live = _game(3)
    gone = _conceded(live, 2)
    assert _code(live, make_event(event_type, {"seat": 5})) == "unknown_seat"
    assert _code(gone, make_event(event_type, {"seat": 2})) == "seat_eliminated"
    assert _code(_in_phase(live, "lobby"), make_event(event_type, {"seat": 5})) == "invalid_phase"


@pytest.mark.parametrize(("event_type", "key", "other"), SETS)
def test_ending_is_allowed_and_other_phases_are_invalid_phase(
    event_type: str, key: str, other: str
) -> None:
    event = make_event(event_type, {"seat": 2})
    assert _code(_in_phase(_game(), "ending"), event) == "ok"
    for phase in OTHER_PHASES:
        assert _code(_in_phase(_game(), phase), event) == "invalid_phase"


@pytest.mark.parametrize(("event_type", "key", "other"), SETS)
def test_a_rejected_holder_event_changes_nothing(event_type: str, key: str, other: str) -> None:
    before = accept(_game(), make_event(event_type, {"seat": 2}))
    snapshot = _canon(before)
    assert _code(before, make_event(event_type, {"seat": 9})) == "invalid_payload"
    assert _canon(before) == snapshot
