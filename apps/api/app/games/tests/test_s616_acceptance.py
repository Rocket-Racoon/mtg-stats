"""Acceptance tests of S-6.1.6 (lobby, seat and turn events), the Python twin of
``packages/game-core/src/acceptance-s616.test.ts``.

Written from the story's six acceptance criteria and CONTRACT.md 4.4, not from the reducer. The
shared vectors (``test_vectors.py``) are the primary proof for everything reachable through
events. This file adds what a vector cannot say: states with eliminated seats (no event can
eliminate a seat before S-6.1.3), phases a log cannot reach yet (``ending``, ``finished``,
``abandoned``), and the link between CONTRACT.md 4.4 and the code.

NOTE FOR S-6.1.3: criterion 6's eliminated-seat case has NO shared vector. S-6.1.3 provides the
first events that eliminate a seat and MUST add vectors for the cases of the "criterion 6" block
below; until then they rest on these unit tests (and their TypeScript twin).
"""

from __future__ import annotations

import itertools
import json
import re
from typing import Any

import pytest

from app.games.core import (
    EVENT_TYPES,
    REDUCER_ERROR_CODES,
    ApplyError,
    GameState,
    apply,
    initial_state,
)
from app.games.core.contract import DEFAULT_SETTINGS as CONTRACT_DEFAULTS
from app.games.tests.lobby_helpers import (
    NO_OP_TYPES,
    S_6_1_6_TYPES,
    accept,
    active_game,
    lobby,
    make_event,
    reject_code,
    seated,
)
from app.games.tests.repo_files import VECTORS_DIR, contract_text, require

UNSET: Any = object()


def ev(event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return make_event(event_type, payload)


def joined(seat: int) -> dict[str, Any]:
    return ev("seat_joined", {"seat": seat, "player_id": f"p{seat}", "name": f"Seat {seat}"})


def started(first: int) -> dict[str, Any]:
    return ev("game_started", {"first_seat": first, "started_at": "2026-11-21T12:00:00.000Z"})


def moved(a: int, b: int, correction: Any = UNSET) -> dict[str, Any]:
    payload: dict[str, Any] = {"from": a, "to": b}
    if correction is not UNSET:
        payload["correction"] = correction
    return ev("seat_moved", payload)


def settings_changed(**kw: Any) -> dict[str, Any]:
    return ev("settings_changed", kw)


def lobby_of(*ids: int) -> GameState:
    """A lobby built through events: seats join in the order given."""
    state = lobby()
    for i in ids:
        state = accept(state, joined(i))
    return state


def play(count: int, first: int) -> GameState:
    """A started game through events: seats 1..count, ``first`` active."""
    return accept(lobby_of(*range(1, count + 1)), started(first))


def code_of(state: GameState, event: dict[str, Any]) -> str:
    """``ok`` when the event is accepted, otherwise the error code."""
    result = apply(state, event)
    return str(result.error.code) if isinstance(result, ApplyError) else "ok"


def in_phase(state: GameState, phase: str) -> GameState:
    return state.model_copy(
        update={"phase": phase, "resume_phase": "active" if phase == "abandoned" else None}
    )


# --- criterion 1 ----------------------------------------------------------------------------------


def test_seat_moved_swaps_seat_4_and_seat_2_of_a_four_seat_lobby() -> None:
    assert accept(lobby_of(1, 2, 3, 4), moved(4, 2)).order == [1, 4, 3, 2]


@pytest.mark.parametrize(
    ("a", "b", "order"),
    [(1, 2, [2, 1, 3, 4]), (1, 4, [4, 2, 3, 1]), (2, 3, [1, 3, 2, 4]), (2, 4, [1, 4, 3, 2])],
)
def test_seat_moved_swaps_two_seats_in_the_lobby_order(a: int, b: int, order: list[int]) -> None:
    assert accept(lobby_of(1, 2, 3, 4), moved(a, b)).order == order
    assert accept(lobby_of(1, 2, 3, 4), moved(b, a)).order == order


def test_seat_moved_in_the_lobby_leaves_the_seats_untouched() -> None:
    before = lobby_of(1, 2, 3, 4)
    after = accept(before, moved(4, 2))
    assert after.seats == before.seats
    assert [s.seat for s in after.seats] == [1, 2, 3, 4]


def test_seat_moved_of_a_seat_with_itself_changes_only_last_seq() -> None:
    before = lobby_of(1, 2, 3, 4)
    assert accept(before, moved(3, 3)) == before.model_copy(
        update={"last_seq": 1 + before.last_seq}
    )


def test_seat_moved_events_apply_in_sequence() -> None:
    state = accept(accept(lobby_of(1, 2, 3, 4), moved(4, 2)), moved(1, 2))
    assert state.order == [2, 4, 3, 1]


@pytest.mark.parametrize("correction", [True, False, None])
def test_seat_moved_is_allowed_in_the_lobby_whatever_the_correction_flag(correction: Any) -> None:
    assert accept(lobby_of(1, 2, 3, 4), moved(4, 2, correction)).order == [1, 4, 3, 2]


def test_seat_moved_is_unknown_seat_when_a_seat_is_not_at_the_table() -> None:
    assert reject_code(lobby_of(1, 2, 3, 4), moved(4, 5)) == "unknown_seat"
    assert reject_code(lobby_of(1, 2, 3, 4), moved(5, 2)) == "unknown_seat"


# --- criterion 2 ----------------------------------------------------------------------------------


def test_correction_swaps_seats_and_keeps_seat_3_active_with_turn_and_round_unchanged() -> None:
    before = play(4, 3)
    after = accept(before, moved(4, 2, True))
    assert after.order == [1, 4, 3, 2]
    assert after.active_seat == 3
    assert next(s for s in after.seats if s.seat == 3).player_id == "p3"
    assert (after.turn, after.round, after.first_seat) == (1, 1, 3)


def test_correction_changes_nothing_but_the_order_and_last_seq() -> None:
    before = play(4, 3)
    after = accept(before, moved(4, 2, True))
    assert after == before.model_copy(
        update={"order": [1, 4, 3, 2], "last_seq": before.last_seq + 1}
    )


def test_correction_keeps_the_active_seat_when_it_is_one_of_the_swapped_seats() -> None:
    after = accept(play(4, 3), moved(3, 1, True))
    assert after.order == [3, 2, 1, 4]
    assert (after.active_seat, after.turn, after.round) == (3, 1, 1)


def test_turn_passed_after_a_correction_follows_the_new_order() -> None:
    state = accept(play(4, 3), moved(4, 2, True))
    seen = []
    for _ in range(4):
        state = accept(state, ev("turn_passed"))
        seen.append(state.active_seat)
    assert seen == [2, 1, 4, 3]
    assert (state.turn, state.round) == (5, 2)


@pytest.mark.parametrize("correction", [UNSET, False, None])
def test_seat_moved_without_a_true_correction_is_invalid_phase_in_an_active_game(
    correction: Any,
) -> None:
    assert reject_code(play(4, 3), moved(4, 2, correction)) == "invalid_phase"


@pytest.mark.parametrize("phase", ["finished", "abandoned"])
def test_seat_moved_is_invalid_phase_after_the_ending_phase_even_with_a_correction(
    phase: str,
) -> None:
    assert reject_code(in_phase(play(4, 3), phase), moved(4, 2, True)) == "invalid_phase"


def test_correction_in_the_ending_phase_swaps_and_changes_nothing_but_order_and_last_seq() -> None:
    before = in_phase(play(4, 3), "ending")
    after = accept(before, moved(4, 2, True))
    assert after == before.model_copy(
        update={"order": [1, 4, 3, 2], "last_seq": before.last_seq + 1}
    )
    assert (after.phase, after.active_seat, after.turn, after.round) == ("ending", 3, 1, 1)


def test_correction_in_the_ending_phase_keeps_the_active_seat_when_it_is_swapped() -> None:
    after = accept(in_phase(play(4, 3), "ending"), moved(3, 1, True))
    assert after.order == [3, 2, 1, 4]
    assert (after.phase, after.active_seat, after.turn, after.round) == ("ending", 3, 1, 1)


@pytest.mark.parametrize("correction", [UNSET, False, None])
def test_seat_moved_without_a_true_correction_is_invalid_phase_in_the_ending_phase(
    correction: Any,
) -> None:
    state = in_phase(play(4, 3), "ending")
    assert reject_code(state, moved(4, 2, correction)) == "invalid_phase"


def test_a_non_boolean_correction_in_the_ending_phase_is_invalid_payload_before_the_phase() -> None:
    state = in_phase(play(4, 3), "ending")
    assert reject_code(state, ev("seat_moved", {"from": 4, "to": 2, "correction": "true"})) == (
        "invalid_payload"
    )


def test_seat_moved_in_the_ending_phase_with_a_seat_not_at_the_table_is_unknown_seat() -> None:
    assert reject_code(in_phase(play(4, 3), "ending"), moved(4, 5, True)) == "unknown_seat"


def test_turn_passed_after_a_correction_in_the_ending_phase_follows_the_new_order() -> None:
    state = accept(in_phase(play(4, 3), "ending"), moved(4, 2, True))
    seen = []
    for _ in range(4):
        state = accept(state, ev("turn_passed"))
        seen.append(state.active_seat)
    assert seen == [2, 1, 4, 3]
    assert (state.phase, state.turn, state.round) == ("ending", 5, 2)


@pytest.mark.parametrize("correction", ["1", 1, 0, "true", []])
def test_a_correction_that_is_not_a_boolean_is_invalid_payload(correction: Any) -> None:
    assert reject_code(play(4, 3), moved(4, 2, correction)) == "invalid_payload"


# --- criterion 3 ----------------------------------------------------------------------------------


def test_settings_changed_sets_trust_mode_strict_and_back_to_casual() -> None:
    strict = accept(lobby_of(1, 2), settings_changed(trust_mode="strict"))
    assert strict.settings.trust_mode == "strict"
    assert accept(strict, settings_changed(trust_mode="casual")).settings.trust_mode == "casual"


@pytest.mark.parametrize(
    "value",
    ["STRICT", "Casual", "", "strict ", None, 1, True, ["strict"], {}, "banana"],
)
def test_a_trust_mode_other_than_casual_or_strict_is_invalid_setting(value: Any) -> None:
    assert reject_code(lobby_of(1, 2), settings_changed(trust_mode=value)) == "invalid_setting"


@pytest.mark.parametrize("payload", [{"trust_mode": "strict"}, {"trust_mode": "BANANA"}, {}])
def test_settings_changed_after_game_started_is_invalid_phase_whatever_the_value(
    payload: dict[str, Any],
) -> None:
    assert reject_code(play(2, 1), ev("settings_changed", payload)) == "invalid_phase"


@pytest.mark.parametrize("phase", ["ending", "finished", "abandoned"])
def test_settings_changed_is_invalid_phase_in_every_later_phase(phase: str) -> None:
    state = in_phase(play(2, 1), phase)
    assert reject_code(state, settings_changed(trust_mode="strict")) == "invalid_phase"


def test_strict_mode_set_in_the_lobby_is_still_strict_after_the_start() -> None:
    lobby_state = accept(lobby_of(1, 2), settings_changed(trust_mode="strict"))
    assert accept(lobby_state, started(1)).settings.trust_mode == "strict"


# --- criterion 4 ----------------------------------------------------------------------------------


@pytest.mark.parametrize("seat", range(1, 9))
def test_the_ninth_seat_joined_is_table_full_whichever_taken_id_it_names(seat: int) -> None:
    full = lobby_of(*range(1, 9))
    assert len(full.seats) == 8
    assert reject_code(full, joined(seat)) == "table_full"


def test_with_eight_seats_an_id_outside_one_to_eight_is_invalid_payload_before_table_full() -> None:
    # Pins the reading both reducers share: the payload shape is checked first (4.4.1, 4.1).
    assert reject_code(lobby_of(*range(1, 9)), joined(9)) == "invalid_payload"


def test_seven_seats_still_give_seat_taken_and_accept_a_free_id() -> None:
    seven = lobby_of(*range(1, 8))
    assert reject_code(seven, joined(4)) == "seat_taken"
    assert len(accept(seven, joined(8)).seats) == 8


def test_a_table_that_dropped_from_eight_to_seven_accepts_a_seat_again() -> None:
    seven = accept(lobby_of(*range(1, 9)), ev("seat_left", {"seat": 8}))
    assert len(accept(seven, joined(8)).seats) == 8


def test_game_started_with_one_seat_is_not_enough_players() -> None:
    assert reject_code(lobby_of(1), started(1)) == "not_enough_players"


def test_game_started_with_two_seats_is_accepted() -> None:
    assert accept(lobby_of(1, 2), started(2)).phase == "active"


def test_game_started_with_no_seat_is_unknown_seat_because_first_seat_is_checked_first() -> None:
    # The criterion names only the 1-seat table. 4.4.9 states the check order explicitly.
    assert reject_code(lobby(), started(1)) == "unknown_seat"


def test_a_one_seat_table_with_another_first_seat_is_unknown_seat() -> None:
    assert reject_code(lobby_of(1), started(2)) == "unknown_seat"


def test_a_seat_that_left_no_longer_counts_towards_the_two_seats() -> None:
    one = accept(lobby_of(1, 2), ev("seat_left", {"seat": 2}))
    assert reject_code(one, started(1)) == "not_enough_players"


# --- criterion 5 ----------------------------------------------------------------------------------


@pytest.mark.parametrize("first", [1, 2, 3, 4])
def test_game_started_makes_first_seat_active_at_turn_1_round_1(first: int) -> None:
    after = accept(lobby_of(1, 2, 3, 4), started(first))
    assert (after.phase, after.active_seat, after.first_seat, after.turn, after.round) == (
        "active",
        first,
        first,
        1,
        1,
    )


def test_game_started_with_first_seat_eight_of_eight() -> None:
    after = accept(lobby_of(*range(1, 9)), started(8))
    assert (after.active_seat, after.turn, after.round) == (8, 1, 1)


def test_game_started_puts_every_seat_at_the_default_starting_life() -> None:
    assert [s.life for s in accept(lobby_of(1, 2, 3, 4), started(3)).seats] == [40, 40, 40, 40]


@pytest.mark.parametrize("life", [1, 20, 100, 999])
def test_game_started_puts_every_seat_at_a_changed_starting_life(life: int) -> None:
    changed = accept(lobby_of(1, 2, 3), settings_changed(starting_life=life))
    assert [s.life for s in changed.seats] == [life] * 3
    assert [s.life for s in accept(changed, started(2)).seats] == [life] * 3


def test_a_seat_joining_after_a_starting_life_change_starts_at_the_new_life() -> None:
    state = accept(accept(lobby_of(1, 2), settings_changed(starting_life=25)), joined(3))
    assert [s.life for s in accept(state, started(1)).seats] == [25, 25, 25]


def test_seats_created_after_game_created_start_at_its_starting_life() -> None:
    state = accept(
        lobby(),
        ev("game_created", {"settings": {**CONTRACT_DEFAULTS, "starting_life": 60}}),
    )
    state = accept(accept(state, joined(1)), joined(2))
    assert [s.life for s in accept(state, started(2)).seats] == [60, 60]


def test_a_first_seat_that_is_not_at_the_table_is_unknown_seat() -> None:
    assert reject_code(lobby_of(1, 2, 3, 4), started(5)) == "unknown_seat"
    assert reject_code(lobby_of(1, 2, 4), started(3)) == "unknown_seat"


def test_a_started_game_cannot_be_started_again() -> None:
    assert reject_code(play(3, 1), started(1)) == "invalid_phase"


def test_first_seat_is_null_in_the_lobby_set_by_the_start_and_never_changed() -> None:
    state = lobby_of(1, 2, 3, 4)
    assert state.first_seat is None
    state = accept(state, started(3))
    assert state.first_seat == 3
    for e in (moved(3, 1, True), ev("turn_passed"), ev("turn_passed")):
        state = accept(state, e)
    assert state.first_seat == 3


# --- criterion 6 ----------------------------------------------------------------------------------
# No event can eliminate a seat before S-6.1.3, so these states are built through the models
# (``active_game`` marks the seats in ``out`` as eliminated with every elimination field set).
# S-6.1.3 MUST add shared vectors for these cases once elimination events exist.


def _game(order: list[int], first: int, dead: tuple[int, ...], active: int) -> GameState:
    state = active_game(order, first, active, turn=7, round_=2, out=dead)
    for seat in state.seats:  # the elimination invariant (3.3)
        fields = [
            seat.elimination_reason,
            seat.eliminated_by,
            seat.eliminated_at_seq,
            seat.eliminated_round,
            seat.elimination_cause,
        ]
        assert (seat.eliminated_at_seq is None) == (seat.seat not in dead)
        if seat.seat not in dead:
            assert all(f is None for f in fields)
        else:
            assert seat.elimination_reason is not None
            assert seat.eliminated_round is not None
    return state


NATURAL = [1, 2, 3, 4]


# The criterion's literal example: seat 3 active, seat 4 out, seat 1 is next. The pass walks the
# places of seats 4 and 1, so the round goes up iff the first player's place is one of them.
@pytest.mark.parametrize(("first", "round_after"), [(1, 3), (2, 2), (3, 2), (4, 3)])
def test_seat_3_active_seat_4_out_seat_1_is_next_and_the_round_follows_the_first_player(
    first: int, round_after: int
) -> None:
    after = accept(_game(NATURAL, first, (4,), 3), ev("turn_passed"))
    assert (after.active_seat, after.turn, after.round) == (1, 8, round_after)
    assert after.first_seat == first


# The 19 rows of the worked-examples table of CONTRACT.md 4.4.10, copied here:
# (row, order, first, eliminated, active, next, (turn, round) before, (turn, round) after)
_Row = tuple[int, list[int], int, tuple[int, ...], int, int, tuple[int, int], tuple[int, int]]
_ROWS: list[_Row] = [
    (1, [1, 2, 3, 4], 1, (), 1, 2, (1, 1), (2, 1)),
    (2, [1, 2, 3, 4], 1, (), 4, 1, (4, 1), (5, 2)),
    (3, [1, 2, 3, 4], 3, (), 3, 4, (1, 1), (2, 1)),
    (4, [1, 2, 3, 4], 3, (), 4, 1, (2, 1), (3, 1)),
    (5, [1, 2, 3, 4], 3, (), 2, 3, (4, 1), (5, 2)),
    (6, [1, 2, 3, 4], 3, (), 2, 3, (12, 3), (13, 4)),
    (7, [1, 2, 3, 4], 1, (4,), 3, 1, (3, 1), (4, 2)),
    (8, [1, 2, 3, 4], 3, (4,), 3, 1, (1, 1), (2, 1)),
    (9, [1, 2, 3, 4], 1, (1,), 2, 3, (2, 1), (3, 1)),
    (10, [1, 2, 3, 4], 1, (1,), 4, 2, (4, 1), (5, 2)),
    (11, [1, 2, 3, 4], 1, (1,), 1, 2, (1, 1), (2, 1)),
    (12, [1, 2, 3, 4], 3, (3,), 2, 4, (4, 1), (5, 2)),
    (13, [1, 2, 3, 4], 4, (4,), 3, 1, (4, 1), (5, 2)),
    (14, [3, 2, 1, 4], 1, (), 4, 3, (2, 1), (3, 1)),
    (15, [1, 2, 3, 4], 1, (2, 3, 4), 1, 1, (1, 1), (2, 2)),
    (16, [1, 2, 3, 4], 1, (1, 2, 3), 4, 4, (4, 3), (5, 4)),
    (17, [3, 2, 1, 4], 1, (), 2, 1, (2, 1), (3, 2)),
    (18, [1, 3, 2, 4], 1, (), 3, 2, (3, 1), (4, 1)),
    (19, [4, 2, 3, 1], 1, (), 3, 1, (3, 1), (4, 2)),
]


def test_the_table_has_rows_1_to_19() -> None:
    assert [r[0] for r in _ROWS] == list(range(1, 20))


@pytest.mark.parametrize("phase", ["active", "ending"])
@pytest.mark.parametrize("row", _ROWS, ids=[f"row{r[0]}" for r in _ROWS])
def test_every_row_of_the_turn_passed_table_in_the_active_and_the_ending_phase(
    row: _Row, phase: str
) -> None:
    _n, order, first, dead, active, expected_next, (turn, rnd), (turn_after, round_after) = row
    base = active_game(order, first, active, turn=turn, round_=rnd, out=dead)
    before = in_phase(base, phase) if phase != "active" else base
    after = accept(before, ev("turn_passed"))
    assert (after.active_seat, after.turn, after.round) == (expected_next, turn_after, round_after)
    assert after == before.model_copy(
        update={
            "active_seat": expected_next,
            "turn": turn_after,
            "round": round_after,
            "last_seq": before.last_seq + 1,
        }
    )


# (passes before the correction, from, to, next seat, (turn, round) after the pass)
@pytest.mark.parametrize(
    ("passes", "src", "dst", "expected_next", "after"),
    [(1, 1, 3, 1, (3, 2)), (2, 3, 2, 2, (4, 1)), (2, 1, 4, 1, (4, 2))],
    ids=["row17", "row18", "row19"],
)
def test_rows_17_to_19_reached_through_events_a_correction_then_the_pass(
    passes: int, src: int, dst: int, expected_next: int, after: tuple[int, int]
) -> None:
    state = play(4, 1)
    for _ in range(passes):
        state = accept(state, ev("turn_passed"))
    state = accept(accept(state, moved(src, dst, True)), ev("turn_passed"))
    assert (state.active_seat, state.turn, state.round) == (expected_next, *after)


# (label, order, first player, eliminated, active, next seat, the round goes up). Every case is
# worked out with the 4.4.10 walk: the places after the active seat up to and including the next
# seat; the round goes up iff the first player's place is among them.
_CASES: list[tuple[str, list[int], int, tuple[int, ...], int, int, bool]] = [
    ("first player out between turns, next survivor is the head", NATURAL, 2, (2,), 1, 3, True),
    (
        "first player out on its own turn: the next pass does not start a round",
        NATURAL,
        2,
        (2,),
        2,
        3,
        False,
    ),
    ("first player out, play not yet at the next survivor", NATURAL, 2, (2,), 4, 1, False),
    ("first player 1 out, wrapping from the last seat", NATURAL, 1, (1,), 4, 2, True),
    ("first player 1 out, mid-round", NATURAL, 1, (1,), 3, 4, False),
    ("two eliminated seats in a row are both skipped", NATURAL, 1, (2, 3), 1, 4, False),
    ("two in a row, wrapping back to the first player", NATURAL, 1, (2, 3), 4, 1, True),
    ("two in a row at the head, first player out too", NATURAL, 1, (1, 2), 4, 3, True),
    ("two in a row at the head, mid-round", NATURAL, 1, (1, 2), 3, 4, False),
    ("two eliminated at the end wrap to the head", NATURAL, 1, (3, 4), 2, 1, True),
    ("first player and the seat after it out at the end", NATURAL, 3, (3, 4), 2, 1, True),
    ("wrap-around past an eliminated head to the first player", NATURAL, 2, (1,), 4, 2, True),
    ("a seat eliminated on its own turn is never chosen again", NATURAL, 1, (3,), 3, 4, False),
    ("order is not the seat ids: 4 out, active 1 goes to 2", [3, 1, 4, 2], 1, (4,), 1, 2, False),
    ("order is not the seat ids: wrap-around to 3", [3, 1, 4, 2], 1, (4,), 2, 3, False),
    ("order is not the seat ids: play reaches the first player", [3, 1, 4, 2], 1, (4,), 3, 1, True),
    ("one survivor that is the first player", NATURAL, 3, (1, 2, 4), 3, 3, True),
    ("one survivor, the first player out", NATURAL, 1, (1, 2, 4), 3, 3, True),
]


@pytest.mark.parametrize("case", _CASES, ids=[c[0] for c in _CASES])
def test_turn_passed_skips_eliminated_seats_and_counts_the_round(
    case: tuple[str, list[int], int, tuple[int, ...], int, int, bool],
) -> None:
    _label, order, first, dead, active, expected_next, round_up = case
    before = _game(order, first, dead, active)
    after = accept(before, ev("turn_passed"))
    assert after.active_seat == expected_next
    assert after.turn == 8
    assert after.round == (3 if round_up else 2)
    assert after.first_seat == first
    assert after.order == order
    assert after.seats == before.seats
    assert after.last_seq == before.last_seq + 1


def test_a_first_player_eliminated_on_its_own_turn_passes_a_round_only_at_its_place_again() -> None:
    # F = 2 out on its own turn (row 11's shape), order [1,2,3,4].
    state = _game(NATURAL, 2, (2,), 2)
    state = state.model_copy(update={"turn": 5, "round": 2})
    seen = []
    for _ in range(4):
        state = accept(state, ev("turn_passed"))
        seen.append((state.active_seat, state.turn, state.round))
    # 3 and 4 finish round 2; 4 -> 1 stays in round 2; 1 -> 3 walks the place of 2: round 3.
    assert seen == [(3, 6, 2), (4, 7, 2), (1, 8, 2), (3, 9, 3)]


@pytest.mark.parametrize("phase", ["lobby", "finished", "abandoned"])
def test_turn_passed_is_invalid_phase_outside_active_and_ending(phase: str) -> None:
    assert reject_code(in_phase(_game(NATURAL, 1, (), 1), phase), ev("turn_passed")) == (
        "invalid_phase"
    )


def test_turn_passed_is_invalid_phase_in_the_ending_phase_when_every_seat_is_eliminated() -> None:
    dead = in_phase(_game(NATURAL, 1, (1, 2, 3, 4), 1), "ending")
    assert reject_code(dead, ev("turn_passed")) == "invalid_phase"


def _oracle(order: list[int], first: int, dead: set[int], active: int) -> tuple[int, bool]:
    """4.4.10 written from its words, independent of the reducer: the walk covers the places of
    ``order`` after the active seat up to and including the first seat that is not eliminated
    (the active seat itself comes last); the round goes up iff the place of ``first`` is walked."""
    at = order.index(active)
    walked: list[int] = []
    for k in range(1, len(order) + 1):
        seat = order[(at + k) % len(order)]
        walked.append(seat)
        if seat not in dead:
            return seat, first in walked
    raise AssertionError("every seat is eliminated")


def test_turn_passed_agrees_with_the_oracle_for_every_possible_4_seat_state() -> None:
    """Every order, first player, set of eliminated seats (not all) and active seat."""
    checked = 0
    for order in itertools.permutations([1, 2, 3, 4]):
        for first in (1, 2, 3, 4):
            for mask in range(15):
                dead = tuple(seat for seat in (1, 2, 3, 4) if (mask >> (seat - 1)) & 1)
                for active in (1, 2, 3, 4):
                    expected_next, round_up = _oracle(list(order), first, set(dead), active)
                    after = accept(_game(list(order), first, dead, active), ev("turn_passed"))
                    got = (after.active_seat, after.turn, after.round)
                    want = (expected_next, 8, 3 if round_up else 2)
                    assert got == want, (order, first, dead, active)
                    checked += 1
    assert checked == 24 * 4 * 15 * 4


def test_a_lone_survivor_passes_to_itself_and_gains_a_round_every_time() -> None:
    state = _game(NATURAL, 1, (1, 2, 4), 3)
    for i in range(1, 4):
        state = accept(state, ev("turn_passed"))
        assert (state.active_seat, state.turn, state.round) == (3, 7 + i, 2 + i)


def test_a_circuit_with_seat_2_out_visits_1_3_4_and_counts_one_round_per_circuit() -> None:
    state = active_game(NATURAL, 1, 1, turn=1, round_=1, out=(2,))
    seen = []
    for _ in range(6):
        state = accept(state, ev("turn_passed"))
        seen.append(state.active_seat)
    assert seen == [3, 4, 1, 3, 4, 1]
    assert (state.turn, state.round) == (7, 3)


def test_turn_passed_is_invalid_phase_when_every_seat_is_eliminated() -> None:
    assert reject_code(_game(NATURAL, 1, (1, 2, 3, 4), 1), ev("turn_passed")) == "invalid_phase"


def test_turn_passed_does_not_change_the_state_it_is_given() -> None:
    before = _game(NATURAL, 1, (4,), 3)
    copy = before.model_copy(deep=True)
    accept(before, ev("turn_passed"))
    assert before == copy


# --- contract 4.4 and the code agree on the nine handlers -----------------------------------------


def _between(start: str, end: str) -> str:
    text = contract_text()
    found = re.search(start, text)
    assert found is not None, f"CONTRACT.md has no {start}"
    rest = text[found.start() + 1 :]
    stop = re.search(end, rest)
    return rest if stop is None else rest[: stop.start()]


HANDLERS = list(S_6_1_6_TYPES)
SHARED_RULES = _between(r"\n#### 4\.4\.1 ", r"\n#{2,4} (?!4\.4\.1 )")
ALLOWED = dict(re.findall(r"^\| `([a-z_]+)` *\| (.+?) *\|$", SHARED_RULES, flags=re.MULTILINE))
PHASES = ["lobby", "active", "ending", "finished", "abandoned"]

VALID_EVENT: dict[str, Any] = {
    "game_created": lambda c: ev("game_created", {"settings": dict(CONTRACT_DEFAULTS)}),
    "settings_changed": lambda c: settings_changed(trust_mode="strict"),
    "seat_joined": lambda c: joined(7),
    "seat_left": lambda c: ev("seat_left", {"seat": 3}),
    "seat_moved": lambda c: moved(1, 3, c),
    "deck_assigned": lambda c: ev(
        "deck_assigned",
        {"seat": 1, "deck_version_id": "d", "commanders": [{"card_id": None, "name": "C"}]},
    ),
    "commanders_set": lambda c: ev(
        "commanders_set", {"seat": 1, "commanders": [{"card_id": "c", "name": "C"}]}
    ),
    "game_started": lambda c: started(1),
    "turn_passed": lambda c: ev("turn_passed"),
}


def _state_in(phase: str) -> GameState:
    return seated(3) if phase == "lobby" else in_phase(active_game([1, 2, 3], 1, 1), phase)


def test_the_allowed_when_table_lists_exactly_the_nine_handlers() -> None:
    assert sorted(ALLOWED) == sorted(HANDLERS)


def _clauses(text: str) -> list[tuple[list[str], bool]]:
    """One (phases, needs `correction: true`) pair per ``phase`` is ... mention of a table cell."""
    pattern = r"`phase` is ((?:`[a-z]+`(?: or )?)+)( with `correction: true`)?"
    return [
        (re.findall(r"`([a-z]+)`", found.group(1)), found.group(2) is not None)
        for found in re.finditer(pattern, text)
    ]


def test_the_table_says_ending_follows_active_for_seat_moved_and_turn_passed() -> None:
    assert _clauses(ALLOWED["seat_moved"]) == [(["lobby"], False), (["active", "ending"], True)]
    assert _clauses(ALLOWED["turn_passed"]) == [(["active", "ending"], False)]


@pytest.mark.parametrize("handler", [h for h in HANDLERS if h != "game_created"])
def test_a_handler_is_accepted_in_exactly_the_phases_the_table_allows(handler: str) -> None:
    clauses = _clauses(ALLOWED[handler])
    assert clauses
    for phase in PHASES:
        for correction in (True, False):
            ok = any(phase in phases and (not gated or correction) for phases, gated in clauses)
            code = code_of(_state_in(phase), VALID_EVENT[handler](correction))
            assert code == ("ok" if ok else "invalid_phase"), (handler, phase, correction)


def test_the_19_worked_examples_of_4_4_10_are_the_rows_pinned_by_the_criterion_6_tests() -> None:
    section = _between(r"\n#### 4\.4\.10 ", r"\n#{2,4} ")
    rows = [
        [cell.strip() for cell in found.group(2).split("|")]
        for found in re.finditer(r"^\| (\d+) +\| (.+) \|$", section, flags=re.MULTILINE)
    ]
    assert len(rows) == 19

    def numbers(cell: str) -> list[int]:
        return [] if cell == "none" else [int(x) for x in cell.split(",")]

    parsed = []
    for i, c in enumerate(rows, start=1):
        order, first, dead, active, nxt = (
            json.loads(c[0].replace("`", "")),
            int(c[1]),
            c[2],
            c[3],
            c[4],
        )
        walked, first_walked, before, after = numbers(c[5]), c[6], numbers(c[7]), numbers(c[8])
        # The walk column is the 4.4.10 definition, and "F walked" is the round rule itself.
        assert (first_walked == "yes") == (first in walked), i
        assert after[1] - before[1] == (1 if first_walked == "yes" else 0), i
        assert after[0] - before[0] == 1, i
        assert walked[-1] == int(nxt), i
        parsed.append(
            (
                i,
                order,
                first,
                tuple(numbers(dead)),
                int(active.split()[0]),
                int(nxt),
                tuple(before),
                tuple(after),
            )
        )
    assert parsed == [tuple(r) for r in _ROWS]


def test_the_old_round_head_is_gone_from_4_4_10_and_the_walk_rule_is_stated() -> None:
    section = _between(r"\n#### 4\.4\.10 ", r"\n#{2,4} ")
    assert "round head" not in section.lower()
    assert "**Positions walked**" in section


def test_game_created_is_accepted_exactly_when_last_seq_is_0_in_any_phase() -> None:
    assert "`state.last_seq` is 0" in ALLOWED["game_created"]
    for phase in PHASES:
        event = VALID_EVENT["game_created"](True)
        assert code_of(in_phase(lobby(), phase), event) == "ok", phase
        assert reject_code(_state_in(phase).model_copy(update={"last_seq": 1}), event) == (
            "invalid_phase"
        )


def _listed_codes() -> dict[str, set[str]]:
    text = contract_text()
    codes: dict[str, set[str]] = {}
    for found in re.finditer(r"\n#### 4\.4\.\d+ `([a-z_]+)`\n", text):
        name = found.group(1)
        body = _between(rf"\n#### 4\.4\.\d+ `{name}`\n", r"\n#{2,4} ")
        spans: set[str] = set()
        for line in re.findall(r"^\d+\. .*$", body, flags=re.MULTILINE):
            spans |= {s for s in re.findall(r"`([a-z_]+)`", line) if s in REDUCER_ERROR_CODES}
        codes[name] = spans
    codes["commanders_set"] = codes["deck_assigned"]
    return codes


def test_commanders_set_takes_its_checks_1_to_3_from_deck_assigned_as_the_document_says() -> None:
    assert "Checks 1 to 3 are those of `deck_assigned`" in _between(
        r"\n#### 4\.4\.8 ", r"\n#{2,4} "
    )


_LISTED = {
    "game_created": ["invalid_payload", "invalid_phase", "invalid_setting"],
    "settings_changed": ["invalid_phase", "invalid_setting"],
    "seat_joined": ["invalid_payload", "invalid_phase", "seat_taken", "table_full"],
    "seat_left": ["invalid_payload", "invalid_phase", "unknown_seat"],
    "seat_moved": ["invalid_payload", "invalid_phase", "unknown_seat"],
    "deck_assigned": ["invalid_payload", "invalid_phase", "unknown_seat"],
    "commanders_set": ["invalid_payload", "invalid_phase", "unknown_seat"],
    "game_started": ["invalid_payload", "invalid_phase", "not_enough_players", "unknown_seat"],
    "turn_passed": ["invalid_phase"],
}

_DECK = {"seat": 5, "deck_version_id": "d", "commanders": [{"card_id": None, "name": "C"}]}
_PROBES: dict[str, dict[str, tuple[Any, dict[str, Any]]]] = {
    "game_created": {
        "invalid_payload": (lobby(), ev("game_created", {"settings": {}})),
        "invalid_phase": (
            seated(1).model_copy(update={"last_seq": 1}),
            VALID_EVENT["game_created"](1),
        ),
        "invalid_setting": (
            lobby(),
            ev("game_created", {"settings": {**CONTRACT_DEFAULTS, "poison_threshold": 100}}),
        ),
    },
    "settings_changed": {
        "invalid_phase": (active_game([1, 2, 3], 1, 1), settings_changed(trust_mode="strict")),
        "invalid_setting": (seated(3), settings_changed(starting_life=0)),
    },
    "seat_joined": {
        "invalid_payload": (seated(3), joined(0)),
        "invalid_phase": (active_game([1, 2, 3], 1, 1), joined(7)),
        "seat_taken": (seated(3), joined(2)),
        "table_full": (seated(8), joined(3)),
    },
    "seat_left": {
        "invalid_payload": (seated(3), ev("seat_left", {})),
        "invalid_phase": (active_game([1, 2, 3], 1, 1), ev("seat_left", {"seat": 1})),
        "unknown_seat": (seated(3), ev("seat_left", {"seat": 5})),
    },
    "seat_moved": {
        "invalid_payload": (seated(3), ev("seat_moved", {"from": 1})),
        "invalid_phase": (active_game([1, 2, 3], 1, 1), moved(1, 2)),
        "unknown_seat": (seated(3), moved(1, 5)),
    },
    "deck_assigned": {
        "invalid_payload": (seated(3), ev("deck_assigned", {})),
        "invalid_phase": (active_game([1, 2, 3], 1, 1), VALID_EVENT["deck_assigned"](True)),
        "unknown_seat": (seated(3), ev("deck_assigned", _DECK)),
    },
    "commanders_set": {
        "invalid_payload": (seated(3), ev("commanders_set", {})),
        "invalid_phase": (active_game([1, 2, 3], 1, 1), VALID_EVENT["commanders_set"](True)),
        "unknown_seat": (
            seated(3),
            ev("commanders_set", {"seat": 5, "commanders": [{"card_id": None, "name": ""}]}),
        ),
    },
    "game_started": {
        "invalid_payload": (seated(3), ev("game_started", {})),
        "invalid_phase": (active_game([1, 2, 3], 1, 1), started(1)),
        "unknown_seat": (seated(3), started(5)),
        "not_enough_players": (seated(1), started(1)),
    },
    "turn_passed": {"invalid_phase": (seated(3), ev("turn_passed"))},
}


@pytest.mark.parametrize("handler", HANDLERS)
def test_the_codes_the_document_lists_are_the_codes_the_code_returns(handler: str) -> None:
    listed = sorted(_listed_codes()[handler])
    assert listed == sorted(_LISTED[handler])
    assert sorted(_PROBES[handler]) == listed
    for code, (state, event) in _PROBES[handler].items():
        assert reject_code(state, event) == code, f"{handler} -> {code}"


def test_first_seat_is_a_documented_state_key_null_in_an_empty_lobby() -> None:
    assert re.search(r"^\| `first_seat` +\| integer or null ", contract_text(), flags=re.MULTILINE)
    assert lobby().first_seat is None


# --- deck and commanders (4.4.7, 4.4.8) -----------------------------------------------------------


def _deck(seat: int, deck_id: str) -> dict[str, Any]:
    return ev(
        "deck_assigned",
        {
            "seat": seat,
            "deck_version_id": deck_id,
            "commanders": [{"card_id": "c1", "name": "Atraxa"}],
        },
    )


def _commanders(seat: int) -> dict[str, Any]:
    return ev(
        "commanders_set",
        {
            "seat": seat,
            "commanders": [
                {"card_id": "c2", "name": "Tymna"},
                {"card_id": None, "name": "Thrasios"},
            ],
        },
    )


def _apply_all(state: GameState, *events: dict[str, Any]) -> GameState:
    for event in events:
        state = accept(state, event)
    return state


def test_commanders_set_sets_the_seat_deck_version_id_to_null_and_replaces_the_commanders() -> None:
    seat = _apply_all(lobby_of(1, 2), _deck(1, "d1"), _commanders(1)).seats[0]
    assert seat.deck_version_id is None
    assert [(c.card_id, c.name) for c in seat.commanders] == [("c2", "Tymna"), (None, "Thrasios")]
    assert seat.commander_cast == [0, 0]


def test_commanders_set_leaves_the_other_seats_and_their_decks_alone() -> None:
    seats = _apply_all(lobby_of(1, 2), _deck(2, "d2"), _commanders(1)).seats
    assert seats[1].deck_version_id == "d2"
    assert [(c.card_id, c.name) for c in seats[1].commanders] == [("c1", "Atraxa")]


def test_commanders_set_on_a_seat_with_no_deck_keeps_deck_version_id_null() -> None:
    assert _apply_all(lobby_of(1, 2), _commanders(2)).seats[1].deck_version_id is None


def test_deck_assigned_after_commanders_set_sets_the_deck_again() -> None:
    seat = _apply_all(lobby_of(1, 2), _deck(1, "d1"), _commanders(1), _deck(1, "d2")).seats[0]
    assert seat.deck_version_id == "d2"
    assert [(c.card_id, c.name) for c in seat.commanders] == [("c1", "Atraxa")]
    assert seat.commander_cast == [0]


# --- settings that were never validated (3.5, 4.4.1, 4.4.3) ---------------------------------------
# The initial settings are a placeholder that is not validated: no handler may raise on them.


def _bad_settings(**settings: Any) -> GameState:
    return initial_state(lobby().game_id, settings)


def test_settings_changed_with_no_key_keeps_an_unvalidated_trust_mode_as_it_is() -> None:
    state = accept(_bad_settings(trust_mode="STRICT"), settings_changed())
    trust_mode: Any = state.settings.trust_mode  # unvalidated, on purpose
    assert trust_mode == "STRICT"
    assert state.last_seq == 1


def test_settings_changed_validates_and_replaces_only_the_key_it_carries() -> None:
    state = accept(_bad_settings(trust_mode="STRICT"), settings_changed(starting_life=30))
    assert (state.settings.trust_mode, state.settings.starting_life) == ("STRICT", 30)


def test_a_bad_key_in_the_event_is_invalid_setting_even_when_untouched_settings_are_bad() -> None:
    state = _bad_settings(trust_mode="STRICT")
    assert reject_code(state, settings_changed(starting_life=0)) == "invalid_setting"


def test_seat_joined_copies_an_unvalidated_starting_life_into_the_new_seat() -> None:
    state = accept(_bad_settings(starting_life="40"), joined(1))
    life: Any = state.seats[0].life  # unvalidated, on purpose
    assert life == "40"


def test_game_started_sets_every_seat_life_to_the_unvalidated_starting_life() -> None:
    state = _apply_all(_bad_settings(starting_life="40"), joined(1), joined(2), started(1))
    lives: list[Any] = [s.life for s in state.seats]  # unvalidated, on purpose
    assert lives == ["40", "40"]
    assert state.phase == "active"


# --- vector coverage of the nine handlers (8.4.1) -------------------------------------------------


def _reducer_vectors() -> list[dict[str, Any]]:
    require(VECTORS_DIR)
    loaded = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(VECTORS_DIR.glob("*.json"))]
    return [v for v in loaded if v.get("kind") == "reducer"]


def _applied(vector: dict[str, Any]) -> list[str]:
    error = vector["expect"].get("error")
    limit = len(vector["events"]) if error is None else error["index"]
    return [e["type"] for e in vector["events"][:limit]]


def test_every_one_of_the_29_registered_types_is_applied_successfully_by_some_vector() -> None:
    seen = {t for v in _reducer_vectors() for t in _applied(v)}
    assert [t for t in EVENT_TYPES if t not in seen] == []


@pytest.mark.parametrize("event_type", S_6_1_6_TYPES)
def test_each_handler_appears_in_at_least_one_vector_that_expects_a_state(
    event_type: str,
) -> None:
    in_state = [t for v in _reducer_vectors() if "state" in v["expect"] for t in _applied(v)]
    assert event_type in in_state


def test_every_handler_rejection_code_appears_in_at_least_one_reject_vector() -> None:
    seen = {v["expect"]["error"]["code"] for v in _reducer_vectors() if "error" in v["expect"]}
    for code in (
        "invalid_phase",
        "invalid_setting",
        "invalid_payload",
        "unknown_seat",
        "seat_taken",
        "table_full",
        "not_enough_players",
    ):
        assert code in seen, code


def test_a_vector_named_for_one_of_the_nine_types_has_an_event_of_that_type() -> None:
    """Naming rule of 8.6: ``accept-<type>-*`` and ``reject-<type>-*`` (underscores as hyphens)."""
    require(VECTORS_DIR)
    wrong: list[str] = []
    for path in sorted(VECTORS_DIR.glob("*.json")):
        vector = json.loads(path.read_text(encoding="utf-8"))
        if vector.get("kind") != "reducer" or not path.name.startswith(("accept-", "reject-")):
            continue
        for event_type in S_6_1_6_TYPES:
            prefix = event_type.replace("_", "-")
            named = path.name.startswith((f"accept-{prefix}-", f"reject-{prefix}-"))
            if named and not any(e["type"] == event_type for e in vector["events"]):
                wrong.append(f"{path.name} has no {event_type} event")
    assert wrong == []


def test_the_still_noop_types_are_the_11_that_are_not_s_6_1_6_to_s_6_1_5_types() -> None:
    assert len(NO_OP_TYPES) == 11
    assert len(S_6_1_6_TYPES) == 9
