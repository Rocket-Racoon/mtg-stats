"""Unit tests of the monarch and initiative hand-off (CONTRACT.md 4.5.2 step 4, 4.7.6).

Twin of ``hand-off.test.ts``. The shared vectors (``accept-hand-off-*``) pin every eliminating
event that exists; this file adds what only the routine called directly can say: several seats at
once, an ``order`` that is not 1..n, a missing ``active_seat``, the ``ending`` phase, a holder that
was already out or is not at the table, and purity of the input. The acceptance tests of the story
are written separately, from its criteria.
"""

from __future__ import annotations

from typing import Any

from app.games.core import GameState, canonical_json
from app.games.core.contract import EliminationReason
from app.games.core.elimination import Elimination, eliminate, next_standing_seat
from app.games.tests.lobby_helpers import accept, active_game, make_event


def _out(seat: int, reason: EliminationReason = "life") -> Elimination:
    return Elimination(seat, reason, None, None)


def _holders(state: GameState) -> list[int | None]:
    return [state.monarch, state.initiative]


def _game(first: int = 1, active: int = 1) -> GameState:
    return active_game([1, 2, 3, 4], first, active)


def _with_holders(state: GameState, monarch: int | None, initiative: int | None) -> GameState:
    return state.model_copy(update={"monarch": monarch, "initiative": initiative})


def _with_order(state: GameState, order: list[int]) -> GameState:
    return state.model_copy(update={"order": order})


def _with_active(state: GameState, active: int | None) -> GameState:
    return state.model_copy(update={"active_seat": active})


def _eliminated(state: GameState, *ids: int) -> GameState:
    seats = [
        s.model_copy(
            update={"elimination_reason": "life", "eliminated_at_seq": 1, "eliminated_round": 1}
        )
        if s.seat in ids
        else s
        for s in state.seats
    ]
    return state.model_copy(update={"seats": seats})


def _in_phase(state: GameState, phase: str) -> GameState:
    return state.model_copy(update={"phase": phase})


def _canon(state: GameState) -> str:
    return canonical_json(state.to_json_value())


def _run(state: GameState, *events: dict[str, Any]) -> GameState:
    for event in events:
        state = accept(state, event)
    return state


def test_it_goes_to_the_active_seat_when_it_still_stands() -> None:
    state = eliminate(_with_holders(_game(), 3, 4), 9, [_out(3), _out(4)])
    assert _holders(state) == [1, 1]


def test_it_goes_to_the_next_seat_in_turn_order_when_the_holder_is_the_active_seat() -> None:
    state = _with_active(_with_holders(_game(), 3, 3), 3)
    assert _holders(eliminate(state, 9, [_out(3)])) == [4, 4]


def test_it_wraps_from_the_end_of_the_order_to_its_head() -> None:
    state = _with_active(_with_holders(_game(), 4, None), 4)
    assert _holders(eliminate(state, 9, [_out(4)])) == [1, None]


def test_it_skips_seats_that_were_eliminated_earlier() -> None:
    state = _eliminated(_with_active(_with_holders(_game(), 2, None), 2), 3, 4)
    assert _holders(eliminate(state, 9, [_out(2)])) == [1, None]


def test_it_reads_the_order_as_it_stands() -> None:
    state = _with_active(_with_order(_with_holders(_game(), 3, 3), [4, 2, 3, 1]), 3)
    assert _holders(eliminate(state, 9, [_out(3)])) == [1, 1]
    other = _with_active(_with_order(_with_holders(_game(), 1, None), [3, 2, 1, 4]), 1)
    assert _holders(eliminate(other, 9, [_out(1)])) == [4, None]


def test_gaps_in_the_seat_ids_do_not_matter_only_the_positions_of_the_order() -> None:
    state = _run(
        _game(),
        make_event("player_conceded", {"seat": 2}),
        make_event("monarch_set", {"seat": 1}),
    )
    assert state.monarch == 1
    gapped = state.model_copy(
        update={"seats": [s for s in state.seats if s.seat != 2], "order": [1, 3, 4]}
    )
    assert _holders(eliminate(_with_active(gapped, 1), 9, [_out(1)])) == [3, None]


def test_with_the_active_seat_eliminated_by_the_same_call_the_next_survivor_receives_it() -> None:
    state = _with_holders(_game(), 2, 3)
    assert _holders(eliminate(state, 9, [_out(1), _out(2), _out(3)])) == [4, 4]
    holder_with_active = _with_active(_with_holders(_game(), 3, None), 2)
    assert _holders(eliminate(holder_with_active, 9, [_out(2), _out(3)])) == [4, None]
    wrapping = _with_active(_with_holders(_game(), 4, 3), 2)
    assert _holders(eliminate(wrapping, 9, [_out(2), _out(3), _out(4)])) == [1, 1]


def test_the_two_keys_are_independent() -> None:
    state = _with_holders(_game(), 3, 4)
    assert _holders(eliminate(state, 9, [_out(3)])) == [1, 4]
    assert _holders(eliminate(state, 9, [_out(4)])) == [3, 1]


def test_a_holder_this_call_did_not_eliminate_keeps_it_even_if_out_or_not_at_the_table() -> None:
    out_earlier = _eliminated(_with_holders(_game(), 2, 3), 2)
    assert _holders(eliminate(out_earlier, 9, [_out(4)])) == [2, 3]
    not_there = _with_holders(_game(), 7, 6)
    assert _holders(eliminate(not_there, 9, [_out(4)])) == [7, 6]


def test_a_skipped_entry_hands_nothing_off() -> None:
    state = _eliminated(_with_holders(_game(), 2, None), 2)
    assert _holders(eliminate(state, 9, [_out(2)])) == [2, None]
    assert _holders(eliminate(_with_holders(_game(), 2, None), 9, [_out(7)])) == [2, None]


def test_a_seat_named_twice_is_handed_off_once() -> None:
    state = _with_holders(_game(), 3, None)
    assert _holders(eliminate(state, 9, [_out(3, "poison"), _out(3)])) == [1, None]


def test_with_nobody_left_standing_both_become_null() -> None:
    state = _with_holders(_game(), 2, 4)
    after = eliminate(state, 9, [_out(1), _out(2), _out(3), _out(4)])
    assert _holders(after) == [None, None]
    assert after.no_winner is True
    last = _eliminated(_with_holders(_game(), 3, 3), 1, 2, 4)
    assert _holders(eliminate(_with_active(last, 3), 9, [_out(3)])) == [None, None]


def test_with_nobody_left_the_answer_is_null_even_if_order_names_a_seat_not_at_the_table() -> None:
    # Not reachable by events; the rule must not depend on what ``order`` holds (4.5.2 step 4).
    two = _with_order(_with_holders(active_game([1, 2], 1, 1), 2, 1), [1, 2, 7])
    assert _holders(eliminate(two, 9, [_out(1), _out(2)])) == [None, None]


def test_a_holder_that_is_the_only_survivor_keeps_it_when_another_seat_goes() -> None:
    state = _eliminated(_with_holders(_game(), 3, None), 1, 2)
    assert _holders(eliminate(state, 9, [_out(4)])) == [3, None]


def test_it_works_the_same_in_the_ending_phase() -> None:
    state = _in_phase(_with_holders(_game(), 3, None), "ending")
    assert _holders(eliminate(state, 9, [_out(3)])) == [1, None]
    active_holder = _in_phase(_with_active(_with_holders(_game(), 3, 3), 3), "ending")
    assert _holders(eliminate(active_holder, 9, [_out(3)])) == [4, 4]


def test_with_no_active_seat_the_scan_starts_at_the_head_of_the_order() -> None:
    state = _with_active(_with_holders(_game(), 2, 1), None)
    assert _holders(eliminate(state, 9, [_out(2), _out(1)])) == [3, 3]
    reordered = _with_active(_with_order(_with_holders(_game(), 2, None), [4, 3, 2, 1]), None)
    assert _holders(eliminate(reordered, 9, [_out(2)])) == [4, None]


def test_an_active_seat_that_is_not_in_the_order_starts_at_the_head() -> None:
    state = _with_order(_with_active(_with_holders(_game(), 2, None), 8), [4, 3, 2, 1])
    # 8 is not a seat at the table, so it is not standing: the scan starts at the head.
    assert eliminate(state, 9, [_out(2)]).monarch == 4


def test_it_changes_nothing_but_the_elimination_fields_no_winner_monarch_and_initiative() -> None:
    before = _with_holders(_game(), 3, 3)
    after = eliminate(before, 9, [_out(3)])
    blank: dict[str, Any] = {"seats": [], "monarch": None, "initiative": None}
    assert after.model_copy(update=blank) == before.model_copy(update=blank)
    assert (after.active_seat, after.turn, after.round, after.last_seq) == (
        before.active_seat,
        before.turn,
        before.round,
        before.last_seq,
    )
    assert (after.winner_seat, after.win_condition, after.phase) == (None, None, "active")


def test_it_does_not_mutate_the_input_state() -> None:
    before = _with_holders(_game(), 3, 3)
    snapshot = _canon(before)
    eliminate(before, 9, [_out(3)])
    assert _canon(before) == snapshot


def test_a_state_with_no_holder_keeps_both_null() -> None:
    assert _holders(eliminate(_game(), 9, [_out(3)])) == [None, None]


def test_no_entry_applied_returns_the_state_untouched() -> None:
    state = _with_holders(_game(), 2, 3)
    assert eliminate(state, 9, []) is state


def test_next_standing_seat_is_the_walk_of_turn_passed() -> None:
    state = _eliminated(_game(), 2)
    assert next_standing_seat(state, 1) == 3
    assert next_standing_seat(state, 4) == 1
    assert next_standing_seat(state, None) == 1
    assert next_standing_seat(_eliminated(_game(), 1, 2, 3), 4) == 4
    assert next_standing_seat(_eliminated(_game(), 1, 2, 3, 4), 4) is None


# --- through events ---


def test_criterion_4_the_monarch_eliminated_while_seat_1_is_active_gives_it_to_seat_1() -> None:
    state = _run(
        _game(),
        make_event("monarch_set", {"seat": 3}),
        make_event("player_conceded", {"seat": 3}),
    )
    assert (state.monarch, state.active_seat) == (1, 1)


def test_criterion_4_the_active_monarch_eliminated_gives_it_to_the_next_seat_in_turn_order() -> (
    None
):
    state = _run(
        _game(3, 3),
        make_event("monarch_set", {"seat": 3}),
        make_event("initiative_set", {"seat": 3}),
        make_event("life_changed", {"seat": 3, "delta": -40}),
    )
    assert (state.monarch, state.initiative, state.active_seat) == (4, 4, 3)


def test_every_eliminating_event_hands_the_crown_off() -> None:
    base = _game()
    cases = [
        make_event("life_changed", {"seat": 3, "delta": -40}),
        make_event("player_conceded", {"seat": 3}),
        make_event("player_eliminated", {"seat": 3}),
        make_event("poison_changed", {"seat": 3, "delta": 10}),
        make_event(
            "commander_damage",
            {"source_seat": 2, "commander": 0, "target_seat": 3, "amount": 21},
        ),
    ]
    for event in cases:
        state = _run(base, make_event("monarch_set", {"seat": 3}), event)
        assert state.monarch == 1, event["type"]


def test_the_monarch_made_active_by_turn_passed_goes_to_the_next_seat() -> None:
    state = _run(
        _game(),
        make_event("monarch_set", {"seat": 2}),
        make_event("turn_passed"),
        make_event("player_conceded", {"seat": 2}),
    )
    assert (state.active_seat, state.monarch) == (2, 3)
