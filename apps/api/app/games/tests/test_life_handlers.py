"""Unit tests of the S-6.1.3 handlers (CONTRACT.md 4.5), the Python twin of ``life.test.ts``.

The shared vectors prove the event-reachable behaviour. Here: the ``ending`` phase and the closed
phases (no event reaches them yet), the check order, a life that is not an integer, the safe range
and purity. The acceptance tests of the story are written separately, from its criteria.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.games.core import ApplyOk, GameState, apply, canonical_json
from app.games.core.contract import WIN_CONDITION_CODES, Seat
from app.games.tests.lobby_helpers import (
    accept,
    active_game,
    make_event,
    reject_code,
    seated,
)

PHASES = ("lobby", "active", "ending", "finished", "abandoned")
MAX = 2**53 - 1


def _game(count: int = 3, active: int = 1) -> GameState:
    return active_game(list(range(1, count + 1)), 1, active)


def _in_phase(state: GameState, phase: str) -> GameState:
    return state.model_copy(
        update={"phase": phase, "resume_phase": "active" if phase == "abandoned" else None}
    )


def _with_life(state: GameState, seat_id: int, value: Any) -> GameState:
    """Set a seat's life without validation (``model_copy`` does not validate)."""
    seats = [s.model_copy(update={"life": value}) if s.seat == seat_id else s for s in state.seats]
    return state.model_copy(update={"seats": seats})


def _life(seat: Any, delta: Any, **extra: Any) -> dict[str, Any]:
    return make_event("life_changed", {"seat": seat, "delta": delta, **extra})


def _concede(seat: Any) -> dict[str, Any]:
    return make_event("player_conceded", {"seat": seat})


def _declare(seat: Any, **extra: Any) -> dict[str, Any]:
    return make_event("player_eliminated", {"seat": seat, **extra})


def _seat(state: GameState, seat_id: int) -> Seat:
    return next(s for s in state.seats if s.seat == seat_id)


def _code(state: GameState, event: Any) -> str:
    """``ok`` when accepted, else the error code."""
    result = apply(state, event)
    return "ok" if isinstance(result, ApplyOk) else str(result.error.code)


# --- life_changed --------------------------------------------------------------------------------


def test_a_seat_at_exactly_zero_is_eliminated_with_the_source_and_the_event_seq() -> None:
    state = _with_life(_game(4), 3, 3)
    after = accept(state, _life(3, -3, source_seat=1))
    seat = _seat(after, 3)
    assert (seat.life, seat.elimination_reason, seat.eliminated_by) == (0, "life", 1)
    assert (seat.eliminated_at_seq, seat.eliminated_round, seat.elimination_cause) == (11, 1, None)
    assert after.last_seq == 11


def test_life_is_not_clamped_and_a_missing_source_means_no_eliminator() -> None:
    after = accept(_with_life(_game(4), 2, 5), _life(2, -8))
    seat = _seat(after, 2)
    assert (seat.life, seat.elimination_reason, seat.eliminated_by) == (-3, "life", None)


def test_a_seat_at_one_life_stays_in_the_game() -> None:
    seat = _seat(accept(_with_life(_game(2), 1, 5), _life(1, -4)), 1)
    assert (seat.life, seat.eliminated_at_seq, seat.elimination_reason) == (1, None, None)


def test_a_confirmed_seq_is_the_eliminated_at_seq() -> None:
    after = accept(_game(3), make_event("life_changed", {"seat": 1, "delta": -40}, seq=11))
    assert _seat(after, 1).eliminated_at_seq == 11


def test_the_source_seat_may_be_the_seat_itself_and_null_means_no_source() -> None:
    assert _seat(accept(_game(3), _life(2, -40, source_seat=2)), 2).eliminated_by == 2
    assert _seat(accept(_game(3), _life(2, -40, source_seat=None)), 2).eliminated_by is None


def test_a_positive_delta_never_eliminates_even_from_a_life_at_or_below_zero() -> None:
    seat = _seat(accept(_with_life(_game(2), 1, -5), _life(1, 3)), 1)
    assert (seat.life, seat.eliminated_at_seq) == (-2, None)
    zero = _seat(accept(_with_life(_game(2), 1, 0), _life(1, 1, source_seat=2)), 1)
    assert zero.eliminated_at_seq is None


def test_a_negative_delta_from_a_life_below_zero_eliminates() -> None:
    seat = _seat(accept(_with_life(_game(2), 1, -5), _life(1, -1)), 1)
    assert (seat.life, seat.elimination_reason) == (-6, "life")


def test_only_the_life_the_seq_and_the_elimination_fields_change() -> None:
    state = _game(4)
    after = accept(state, _life(2, -7, source_seat=3))
    assert after.model_copy(update={"seats": state.seats, "last_seq": state.last_seq}) == state
    assert [s for s in after.seats if s.seat != 2] == [s for s in state.seats if s.seat != 2]
    assert _seat(after, 2).life == 33
    assert _seat(after, 2).eliminated_at_seq is None


def test_an_eliminated_active_seat_stays_active_and_nothing_else_moves() -> None:
    after = accept(_game(3, active=2), _life(2, -40))
    assert (after.active_seat, after.turn, after.round, after.order) == (2, 1, 1, [1, 2, 3])


def test_the_last_seat_in_the_game_losing_all_life_is_a_full_draw() -> None:
    state = accept(accept(_game(3), _concede(1)), _concede(2))
    after = accept(state, _life(3, -40))
    assert after.no_winner is True
    assert after.phase == "active"


@pytest.mark.parametrize("delta", [0, 1000, -1000, 99999, -(10**30)])
def test_a_delta_of_zero_or_above_999_is_invalid_amount(delta: int) -> None:
    # an integer outside the safe range never reaches the handler (step 5 of 6.2), so the huge
    # one is invalid_payload
    expected = "invalid_payload" if abs(delta) > MAX else "invalid_amount"
    assert reject_code(_game(), _life(1, delta)) == expected


@pytest.mark.parametrize("delta", [999, -999, 1, -1])
def test_a_delta_within_one_to_999_is_accepted(delta: int) -> None:
    assert _code(_game(), _life(1, delta)) == "ok"


@pytest.mark.parametrize("delta", ["3", None, True, False, 1.5, 3.0, [], {}, [3]])
def test_a_delta_that_is_not_an_integer_is_invalid_payload(delta: Any) -> None:
    assert reject_code(_game(), _life(1, delta)) == "invalid_payload"


def test_an_absent_delta_is_invalid_payload() -> None:
    assert reject_code(_game(), make_event("life_changed", {"seat": 1})) == "invalid_payload"


@pytest.mark.parametrize("seat", [0, 9, -1, True, False, "1", None, 1.0, 1.5, [1], {}])
def test_a_seat_that_is_not_a_seat_id_is_invalid_payload(seat: Any) -> None:
    assert reject_code(_game(), _life(seat, -1)) == "invalid_payload"
    assert reject_code(_game(), _concede(seat)) == "invalid_payload"
    assert reject_code(_game(), _declare(seat)) == "invalid_payload"


def test_an_absent_seat_is_invalid_payload() -> None:
    for event_type in ("life_changed", "player_conceded", "player_eliminated"):
        assert reject_code(_game(), make_event(event_type, {"delta": -1})) == "invalid_payload"


@pytest.mark.parametrize("source", [0, 9, True, False, "1", 1.0, [1], {}])
def test_a_source_seat_that_is_not_a_seat_id_is_invalid_payload(source: Any) -> None:
    assert reject_code(_game(), _life(1, -1, source_seat=source)) == "invalid_payload"


# --- a life that is not an integer (4.5.1) -------------------------------------------------------

BAD_LIVES: list[Any] = [
    "40",
    None,
    True,
    False,
    {},
    [40],
    40.0,
    40.5,
    float("nan"),
    float("inf"),
    2**60,
    -(2**60),
]


@pytest.mark.parametrize("value", BAD_LIVES, ids=repr)
def test_life_changed_on_a_life_that_is_not_an_integer_is_invalid_amount_and_never_raises(
    value: Any,
) -> None:
    state = _with_life(_game(), 1, value)
    assert reject_code(state, _life(1, -3)) == "invalid_amount"
    assert reject_code(state, _life(1, 3)) == "invalid_amount"


def test_life_changed_does_not_read_the_life_of_another_seat() -> None:
    assert _code(_with_life(_game(), 2, "40"), _life(1, -3)) == "ok"
    assert _code(_with_life(_game(), 2, "40"), _life(1, -3, source_seat=2)) == "ok"


@pytest.mark.parametrize("value", BAD_LIVES, ids=repr)
def test_a_concession_and_a_declared_elimination_work_on_a_life_that_is_not_an_integer(
    value: Any,
) -> None:
    state = _with_life(_game(), 1, value)
    assert _code(state, _concede(1)) == "ok"
    assert _code(state, _declare(1, cause="mill")) == "ok"
    assert _seat(accept(state, _concede(1)), 1).eliminated_at_seq == 11


def test_the_new_life_must_stay_in_the_safe_range() -> None:
    assert _code(_with_life(_game(2), 1, MAX), _life(1, 1)) == "invalid_amount"
    assert _code(_with_life(_game(2), 1, MAX - 1), _life(1, 1)) == "ok"
    assert _code(_with_life(_game(2), 1, -MAX), _life(1, -1)) == "invalid_amount"
    assert _code(_with_life(_game(2), 1, -MAX + 1), _life(1, -1)) == "ok"
    assert _seat(accept(_with_life(_game(2), 1, MAX - 1), _life(1, 1)), 1).life == MAX
    assert _seat(accept(_with_life(_game(2), 1, -MAX + 1), _life(1, -1)), 1).life == -MAX


# --- concession and declared elimination ---------------------------------------------------------


@pytest.mark.parametrize("life", [40, 1, 0, -7, 500])
def test_a_concession_eliminates_the_seat_whatever_its_life(life: int) -> None:
    seat = _seat(accept(_with_life(_game(), 2, life), _concede(2)), 2)
    assert (seat.elimination_reason, seat.eliminated_by, seat.elimination_cause) == (
        "concession",
        None,
        None,
    )
    assert (seat.eliminated_at_seq, seat.eliminated_round, seat.life) == (11, 1, life)


def test_a_concession_by_the_active_seat_keeps_it_active_until_the_turn_passes() -> None:
    after = accept(_game(3, active=2), _concede(2))
    assert (after.active_seat, after.turn) == (2, 1)
    assert accept(after, make_event("turn_passed")).active_seat == 3


@pytest.mark.parametrize("cause", WIN_CONDITION_CODES)
def test_every_d5_code_is_accepted_as_a_cause_and_recorded(cause: str) -> None:
    seat = _seat(accept(_game(), _declare(2, cause=cause)), 2)
    assert (seat.elimination_reason, seat.elimination_cause, seat.eliminated_by) == (
        "declared",
        cause,
        None,
    )


def test_an_absent_or_null_cause_is_stored_as_null() -> None:
    assert _seat(accept(_game(), _declare(2)), 2).elimination_cause is None
    assert _seat(accept(_game(), _declare(2, cause=None)), 2).elimination_cause is None


@pytest.mark.parametrize(
    "cause", ["draw", "Mill", "", "mill ", 7, True, False, 0, [], {}, ["mill"], 1.5]
)
def test_a_cause_that_is_not_a_d5_code_is_invalid_payload(cause: Any) -> None:
    assert reject_code(_game(), _declare(2, cause=cause)) == "invalid_payload"


def test_a_declared_elimination_sets_no_winner_fields_and_does_not_read_life() -> None:
    state = _with_life(_game(2), 1, 12)
    after = accept(state, _declare(1, cause="combo"))
    assert (after.winner_seat, after.win_condition, after.no_winner) == (None, None, False)
    assert _seat(after, 1).life == 12


def test_the_two_seats_of_a_two_seat_game_conceding_in_turn_is_a_full_draw_at_the_second() -> None:
    after = accept(_game(2), _concede(1))
    assert after.no_winner is False
    final = accept(after, _concede(2))
    assert final.no_winner is True
    assert [s.eliminated_at_seq for s in final.seats] == [11, 12]


# --- events on an eliminated seat (criterion 4) --------------------------------------------------


def _dead() -> GameState:
    return accept(_game(4), _concede(2))


def test_every_handler_rejects_an_eliminated_seat_with_seat_eliminated() -> None:
    assert reject_code(_dead(), _life(2, -1)) == "seat_eliminated"
    assert reject_code(_dead(), _life(2, 1)) == "seat_eliminated"
    assert reject_code(_dead(), _concede(2)) == "seat_eliminated"
    assert reject_code(_dead(), _declare(2)) == "seat_eliminated"


def test_an_eliminated_source_seat_is_rejected_also_on_a_gain() -> None:
    assert reject_code(_dead(), _life(1, -1, source_seat=2)) == "seat_eliminated"
    assert reject_code(_dead(), _life(1, 1, source_seat=2)) == "seat_eliminated"


def test_seat_eliminated_comes_after_unknown_seat_and_before_invalid_amount() -> None:
    assert reject_code(_dead(), _life(2, 0, source_seat=7)) == "unknown_seat"
    assert reject_code(_dead(), _life(2, 0)) == "seat_eliminated"
    assert reject_code(_dead(), _life(1, 0, source_seat=2)) == "seat_eliminated"
    assert reject_code(_dead(), _life(2, 1000, source_seat=1)) == "seat_eliminated"


def test_unknown_seat_is_checked_for_both_seats_before_seat_eliminated_for_either() -> None:
    # seat 2 is eliminated and the source seat 7 is not at the table: unknown_seat wins
    assert reject_code(_dead(), _life(2, -1, source_seat=7)) == "unknown_seat"
    assert reject_code(_dead(), _life(1, -1, source_seat=7)) == "unknown_seat"


def test_the_seat_is_checked_before_the_source_seat_for_seat_eliminated() -> None:
    state = accept(_dead(), _concede(3))
    assert reject_code(state, _life(2, -1, source_seat=3)) == "seat_eliminated"
    assert reject_code(state, _life(4, -1, source_seat=2)) == "seat_eliminated"


def test_a_seat_eliminated_by_life_is_rejected_like_one_eliminated_by_concession() -> None:
    state = accept(_game(3), _life(1, -40))
    assert reject_code(state, _life(1, -1)) == "seat_eliminated"


def test_a_host_correction_may_still_move_an_eliminated_seat_in_the_order() -> None:
    after = accept(_dead(), make_event("seat_moved", {"from": 2, "to": 4, "correction": True}))
    assert after.order == [1, 4, 3, 2]
    assert _seat(after, 2).eliminated_at_seq is not None


# --- phases and check order ----------------------------------------------------------------------

_BUILDERS = {
    "life_changed": lambda: _life(1, -1),
    "player_conceded": lambda: _concede(1),
    "player_eliminated": lambda: _declare(1),
}


@pytest.mark.parametrize("event_type", list(_BUILDERS))
@pytest.mark.parametrize("phase", PHASES)
def test_each_handler_is_accepted_in_active_and_ending_and_invalid_phase_elsewhere(
    event_type: str, phase: str
) -> None:
    expected = "ok" if phase in ("active", "ending") else "invalid_phase"
    assert _code(_in_phase(_game(), phase), _BUILDERS[event_type]()) == expected


def test_ending_gives_the_same_result_as_active() -> None:
    event = _life(2, -41, source_seat=1)
    a = accept(_game(), event)
    b = accept(_in_phase(_game(), "ending"), event)
    assert canonical_json(a.model_copy(update={"phase": "ending"}).to_json_value()) == (
        canonical_json(b.to_json_value())
    )


def test_a_full_draw_does_not_move_the_phase() -> None:
    final = accept(accept(_game(2), _concede(1)), _concede(2))
    assert (final.phase, final.no_winner, final.winner_seat, final.win_condition) == (
        "active",
        True,
        None,
        None,
    )


def test_the_check_order_is_shape_phase_unknown_seat_eliminated_amount() -> None:
    lobby = seated(3)
    assert reject_code(lobby, _life(1, "x")) == "invalid_payload"
    assert reject_code(lobby, _life(9, -1)) == "invalid_payload"
    assert reject_code(lobby, _life(5, -1)) == "invalid_phase"
    assert reject_code(_game(), _life(5, 0)) == "unknown_seat"
    assert reject_code(_game(), _life(1, -1, source_seat=5)) == "unknown_seat"
    assert reject_code(_game(), _life(1, 0)) == "invalid_amount"


def test_a_bad_cause_is_invalid_payload_even_in_the_lobby_and_on_an_unknown_seat() -> None:
    assert reject_code(seated(3), _declare(1, cause="draw")) == "invalid_payload"
    assert reject_code(_game(), _declare(7, cause="draw")) == "invalid_payload"
    assert reject_code(seated(3), _declare(1, cause="mill")) == "invalid_phase"
    assert reject_code(_game(), _declare(7, cause="mill")) == "unknown_seat"


def test_a_state_with_no_seats_gives_unknown_seat() -> None:
    state = _game().model_copy(update={"seats": [], "order": []})
    assert reject_code(state, _life(1, -1)) == "unknown_seat"
    assert reject_code(state, _concede(1)) == "unknown_seat"


def test_unknown_payload_keys_are_ignored() -> None:
    assert _code(_game(), make_event("life_changed", {"seat": 1, "delta": -1, "x": 1})) == "ok"
    assert _code(_game(), make_event("player_conceded", {"seat": 1, "delta": "x"})) == "ok"
    assert _code(_game(), _declare(1, cause="mill", seat_name="x")) == "ok"


def test_an_absent_and_a_null_source_seat_mean_the_same() -> None:
    absent = accept(_game(), _life(1, -40))
    null = accept(_game(), _life(1, -40, source_seat=None))
    assert canonical_json(absent.to_json_value()) == canonical_json(null.to_json_value())


def test_a_rejected_event_changes_nothing() -> None:
    state = _game()
    snapshot = canonical_json(state.to_json_value())
    assert reject_code(state, _life(1, 0)) == "invalid_amount"
    assert canonical_json(state.to_json_value()) == snapshot


def test_the_input_state_is_not_mutated_by_an_accepted_event() -> None:
    state = _game(4)
    snapshot = canonical_json(state.to_json_value())
    accept(state, _life(2, -41, source_seat=1))
    accept(state, _concede(3))
    accept(state, _declare(4, cause="other"))
    assert canonical_json(state.to_json_value()) == snapshot
