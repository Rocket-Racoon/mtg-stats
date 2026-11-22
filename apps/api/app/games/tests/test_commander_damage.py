"""Unit tests of the S-6.1.4 handler (CONTRACT.md 4.6), twin of ``commander-damage.test.ts``.

The shared vectors prove the event-reachable behaviour. Here: the ``ending`` phase and the closed
phases (no event reaches them yet), the check order, hand-built states (unvalidated numbers, the
safe range), purity and the canonical key order. The acceptance tests of the story are written
separately, from its criteria.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.games.core import ApplyError, ApplyOk, GameState, apply, canonical_json
from app.games.core.contract import Commander, Seat
from app.games.tests.lobby_helpers import accept, active_game, make_event, reject_code

PHASES = ("lobby", "active", "ending", "finished", "abandoned")
MAX = 2**53 - 1


def _game(count: int = 3) -> GameState:
    return active_game(list(range(1, count + 1)), 1, 1)


def _cd(source: Any, commander: Any, target: Any, amount: Any) -> dict[str, Any]:
    return make_event(
        "commander_damage",
        {"source_seat": source, "commander": commander, "target_seat": target, "amount": amount},
    )


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


def _with_partners(state: GameState, seat_id: int) -> GameState:
    return _with_seat(
        state,
        seat_id,
        commanders=[Commander(card_id=None, name="A"), Commander(card_id=None, name="B")],
        commander_cast=[0, 0],
    )


def _with_threshold(state: GameState, value: Any) -> GameState:
    return state.model_copy(
        update={"settings": state.settings.model_copy(update={"commander_damage_threshold": value})}
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


def _out(state: GameState, *seats: int) -> GameState:
    """Seats eliminated by hand (an event cannot do it without a handler of its own)."""
    for n in seats:
        state = _with_seat(
            state,
            n,
            elimination_reason="concession",
            eliminated_at_seq=5,
            eliminated_round=1,
        )
    return state


# --- criteria 1 to 4 and 6 with hand-derived numbers ---


def test_three_hits_of_7_give_21_and_19_life_and_an_elimination() -> None:
    before = _game(4)
    state = _run(before, _cd(1, 0, 3, 7), _cd(1, 0, 3, 7), _cd(1, 0, 3, 7))
    assert state.commander_damage == {"3": {"1:0": 21}}
    seat = _seat(state, 3)
    assert (seat.life, seat.elimination_reason, seat.eliminated_by) == (19, "commander_damage", 1)
    assert (seat.eliminated_at_seq, seat.eliminated_round, seat.elimination_cause) == (
        before.last_seq + 3,
        1,
        None,
    )
    assert state.last_seq == before.last_seq + 3


def test_partners_12_and_12_are_two_tallies_and_16_life_and_nobody_is_eliminated() -> None:
    state = _run(_with_partners(_game(4), 2), _cd(2, 0, 4, 12), _cd(2, 1, 4, 12))
    assert state.commander_damage == {"4": {"2:0": 12, "2:1": 12}}
    assert _seat(state, 4).life == 16
    assert _seat(state, 4).eliminated_at_seq is None
    third = accept(state, _cd(2, 0, 4, 9))
    assert third.commander_damage == {"4": {"2:0": 21, "2:1": 12}}
    assert (_seat(third, 4).life, _seat(third, 4).eliminated_by) == (7, 2)


def test_the_tally_the_life_and_the_elimination_change_in_the_one_event() -> None:
    before = _run(_game(4), _cd(1, 0, 3, 14))
    result = apply(before, _cd(1, 0, 3, 7))
    assert isinstance(result, ApplyOk)
    assert before.commander_damage == {"3": {"1:0": 14}}
    assert (_seat(before, 3).life, _seat(before, 3).eliminated_at_seq) == (26, None)
    assert result.state.commander_damage == {"3": {"1:0": 21}}
    assert _seat(result.state, 3).life == 19
    assert _seat(result.state, 3).eliminated_at_seq == before.last_seq + 1


def test_commander_damage_wins_when_the_tally_and_the_life_both_qualify() -> None:
    state = _run(_with_seat(_game(4), 4, life=21), _cd(1, 0, 4, 21))
    seat = _seat(state, 4)
    assert (seat.life, seat.elimination_reason, seat.eliminated_by) == (0, "commander_damage", 1)


def test_a_tally_far_above_the_threshold_is_still_commander_damage() -> None:
    state = _run(_with_seat(_game(4), 4, life=10), _cd(1, 0, 4, 25))
    assert (_seat(state, 4).life, _seat(state, 4).elimination_reason) == (-15, "commander_damage")


def test_the_threshold_comes_from_the_settings() -> None:
    before = _with_threshold(_game(3), 15)
    two = _run(before, _cd(1, 0, 2, 7), _cd(1, 0, 2, 7))
    assert _seat(two, 2).eliminated_at_seq is None
    three = accept(two, _cd(1, 0, 2, 1))
    assert (_seat(three, 2).elimination_reason, _seat(three, 2).life) == ("commander_damage", 25)


# --- the life-only elimination ---


def test_a_seat_is_eliminated_by_life_with_the_source_when_the_tally_stays_below() -> None:
    state = _run(_with_seat(_game(3), 2, life=5), _cd(1, 0, 2, 5))
    seat = _seat(state, 2)
    assert (seat.life, seat.elimination_reason, seat.eliminated_by) == (0, "life", 1)
    assert state.commander_damage == {"2": {"1:0": 5}}


def test_life_is_not_clamped_when_a_hit_below_the_threshold_eliminates_by_life() -> None:
    state = _run(_with_seat(_game(3), 2, life=5), _cd(1, 0, 2, 20))
    seat = _seat(state, 2)
    assert (seat.life, seat.elimination_reason) == (-15, "life")


def test_only_the_tally_of_the_one_source_key_counts_never_a_sum_over_partners() -> None:
    state = _run(_with_partners(_game(3), 1), _cd(1, 0, 2, 20), _cd(1, 1, 2, 20))
    seat = _seat(state, 2)
    assert (seat.life, seat.elimination_reason, seat.eliminated_by) == (0, "life", 1)
    assert state.commander_damage == {"2": {"1:0": 20, "1:1": 20}}


def test_a_seat_in_the_game_at_zero_life_is_eliminated_by_life_on_the_next_hit() -> None:
    state = accept(_with_seat(_game(3), 2, life=0), _cd(1, 0, 2, 1))
    assert (_seat(state, 2).life, _seat(state, 2).elimination_reason) == (-1, "life")


# --- self damage ---


def test_self_damage_is_legal_and_names_the_seat_itself_as_the_eliminator() -> None:
    state = accept(_game(3), _cd(2, 0, 2, 21))
    seat = _seat(state, 2)
    assert (seat.life, seat.elimination_reason, seat.eliminated_by) == (19, "commander_damage", 2)
    assert state.commander_damage == {"2": {"2:0": 21}}


# --- phases ---


def test_the_ending_phase_accepts_commander_damage() -> None:
    state = accept(_in_phase(_game(3), "ending"), _cd(1, 0, 2, 3))
    assert state.phase == "ending"
    assert _seat(state, 2).life == 37


@pytest.mark.parametrize("phase", [p for p in PHASES if p not in ("active", "ending")])
def test_a_closed_or_lobby_phase_is_invalid_phase_even_with_a_bad_amount_or_seat(
    phase: str,
) -> None:
    state = _in_phase(_game(3), phase)
    assert reject_code(state, _cd(1, 0, 2, 1)) == "invalid_phase"
    assert reject_code(state, _cd(1, 0, 2, 0)) == "invalid_phase"
    assert reject_code(state, _cd(1, 0, 7, 1)) == "invalid_phase"


def test_the_payload_shape_is_checked_before_the_phase() -> None:
    assert reject_code(_in_phase(_game(3), "finished"), _cd(1, 0, 2, "1")) == "invalid_payload"


# --- check order ---


def test_the_checks_go_shape_phase_seat_eliminated_amount_commander_life() -> None:
    game = _with_partners(_game(4), 2)
    gone = _out(game, 3)
    assert reject_code(gone, _cd(1, 9, 2, "x")) == "invalid_payload"
    assert reject_code(gone, _cd(7, 5, 3, 0)) == "unknown_seat"
    assert reject_code(gone, _cd(1, 5, 3, 0)) == "seat_eliminated"
    assert reject_code(gone, _cd(3, 5, 1, 0)) == "seat_eliminated"
    assert reject_code(game, _cd(1, 5, 2, 0)) == "invalid_amount"
    assert reject_code(_with_seat(game, 2, life="x"), _cd(1, 5, 2, 1)) == "unknown_commander"
    assert reject_code(_with_seat(game, 2, life="x"), _cd(1, 0, 2, 1)) == "invalid_amount"


@pytest.mark.parametrize(
    ("amount", "code"),
    [
        (0, "invalid_amount"),
        (-1, "invalid_amount"),
        (1, "ok"),
        (999, "ok"),
        (1000, "invalid_amount"),
    ],
)
def test_the_amount_must_be_from_1_to_999(amount: int, code: str) -> None:
    assert _code(_game(3), _cd(1, 0, 2, amount)) == code


def test_the_commander_is_checked_against_the_source_seat_not_the_target() -> None:
    game = _with_partners(_game(4), 2)
    assert _code(game, _cd(2, 0, 1, 1)) == "ok"
    assert _code(game, _cd(2, 1, 1, 1)) == "ok"
    assert _code(game, _cd(2, 2, 1, 1)) == "unknown_commander"
    assert _code(game, _cd(2, -1, 1, 1)) == "unknown_commander"
    assert _code(game, _cd(1, 1, 2, 1)) == "unknown_commander"


@pytest.mark.parametrize("bad", [True, False, "0", 0.5, 1.0, None, [], {}])
def test_a_bool_a_string_a_float_or_null_is_not_an_integer_for_commander_or_amount(
    bad: Any,
) -> None:
    game = _with_partners(_game(3), 1)
    assert reject_code(game, _cd(1, bad, 2, 1)) == "invalid_payload"
    assert reject_code(game, _cd(1, 0, 2, bad)) == "invalid_payload"


def test_a_missing_key_is_invalid_payload() -> None:
    for key in ("source_seat", "commander", "target_seat", "amount"):
        payload = {"source_seat": 1, "commander": 0, "target_seat": 2, "amount": 1}
        del payload[key]
        assert reject_code(_game(3), make_event("commander_damage", payload)) == "invalid_payload"


@pytest.mark.parametrize("bad", [True, "1", 0, 9, -1, None, 1.0])
def test_a_seat_id_must_be_an_integer_from_1_to_8(bad: Any) -> None:
    assert reject_code(_game(3), _cd(bad, 0, 2, 1)) == "invalid_payload"
    assert reject_code(_game(3), _cd(1, 0, bad, 1)) == "invalid_payload"


def test_a_seat_not_at_the_table_is_unknown_source_first_then_target() -> None:
    assert reject_code(_game(3), _cd(7, 0, 2, 1)) == "unknown_seat"
    assert reject_code(_game(3), _cd(1, 0, 7, 1)) == "unknown_seat"
    assert reject_code(_out(_game(3), 2), _cd(7, 0, 2, 1)) == "unknown_seat"


# --- unvalidated numbers (3.5) and the safe range ---


@pytest.mark.parametrize("value", ["40", True, False, None, 1.5, 40.0, [], {}])
def test_a_life_that_is_not_an_integer_is_invalid_amount(value: Any) -> None:
    assert reject_code(_with_seat(_game(3), 2, life=value), _cd(1, 0, 2, 1)) == "invalid_amount"


@pytest.mark.parametrize("value", ["21", True, False, None, 21.5, 21.0, [], {}])
def test_a_threshold_that_is_not_an_integer_is_invalid_amount(value: Any) -> None:
    assert reject_code(_with_threshold(_game(3), value), _cd(1, 0, 2, 1)) == "invalid_amount"


@pytest.mark.parametrize("value", [0, -5, 100, 1000])
def test_an_integer_threshold_outside_1_to_99_is_used_as_given(value: int) -> None:
    state = accept(_with_threshold(_game(3), value), _cd(1, 0, 2, 1))
    assert (_seat(state, 2).eliminated_at_seq is not None) == (value <= 1)
    if value <= 1:
        assert _seat(state, 2).elimination_reason == "commander_damage"


def test_a_life_that_would_leave_the_safe_range_is_rejected_and_the_last_one_inside_is_not() -> (
    None
):
    edge = -MAX
    assert reject_code(_with_seat(_game(3), 2, life=edge), _cd(1, 0, 2, 1)) == "invalid_amount"
    assert _code(_with_seat(_game(3), 2, life=edge + 5), _cd(1, 0, 2, 5)) == "ok"


def test_a_life_beyond_the_safe_range_is_not_an_integer() -> None:
    assert reject_code(_with_seat(_game(3), 2, life=MAX + 1), _cd(1, 0, 2, 1)) == "invalid_amount"
    assert reject_code(_with_seat(_game(3), 2, life=-MAX - 1), _cd(1, 0, 2, 1)) == "invalid_amount"


def test_a_tally_that_would_leave_the_safe_range_is_invalid_amount() -> None:
    state = _game(3).model_copy(update={"commander_damage": {"2": {"1:0": MAX}}})
    assert reject_code(state, _cd(1, 0, 2, 1)) == "invalid_amount"
    ok = _game(3).model_copy(update={"commander_damage": {"2": {"1:0": MAX - 1}}})
    assert _code(ok, _cd(1, 0, 2, 1)) == "ok"


# --- the state ---


def test_the_row_and_the_key_are_created_by_the_first_damage_and_never_removed() -> None:
    start = _game(3)
    assert start.commander_damage == {}
    one = accept(start, _cd(1, 0, 2, 4))
    assert one.commander_damage == {"2": {"1:0": 4}}
    gained = accept(one, make_event("life_changed", {"seat": 2, "delta": 10}))
    assert gained.commander_damage == {"2": {"1:0": 4}}
    two = _run(gained, _cd(3, 0, 1, 2), _cd(1, 0, 2, 1))
    assert two.commander_damage == {"1": {"3:0": 2}, "2": {"1:0": 5}}


def test_the_tally_of_an_eliminated_source_and_target_stays() -> None:
    state = _run(_game(3), _cd(1, 0, 2, 21))
    after = accept(state, make_event("player_conceded", {"seat": 1}))
    assert after.commander_damage == {"2": {"1:0": 21}}


def test_the_keys_come_out_in_canonical_order_whatever_the_order_of_the_damage() -> None:
    state = _run(
        _with_partners(_game(4), 4),
        _cd(4, 1, 3, 2),
        _cd(3, 0, 1, 3),
        _cd(2, 0, 4, 4),
        _cd(4, 0, 3, 6),
        _cd(1, 0, 4, 7),
    )
    assert canonical_json(state.to_json_value()["commander_damage"]) == (
        '{"1":{"3:0":3},"3":{"4:0":6,"4:1":2},"4":{"1:0":7,"2:0":4}}'
    )


def test_only_the_target_its_row_the_elimination_fields_and_last_seq_change() -> None:
    before = _game(4)
    after = accept(before, _cd(1, 0, 3, 21))
    assert [s for s in after.seats if s.seat != 3] == [s for s in before.seats if s.seat != 3]
    neutral = {"seats": [], "commander_damage": {}, "last_seq": 0, "no_winner": False}
    assert after.model_copy(update=neutral) == before.model_copy(update=neutral)


def test_the_input_state_is_never_mutated() -> None:
    before = _run(_game(4), _cd(1, 0, 3, 7))
    snapshot = _canon(before)
    _run(before, _cd(1, 0, 3, 7), _cd(1, 0, 3, 7))
    assert _canon(before) == snapshot


def test_a_rejected_event_changes_nothing() -> None:
    before = _run(_game(4), _cd(1, 0, 3, 7))
    snapshot = _canon(before)
    result = apply(before, _cd(1, 1, 3, 7))
    assert isinstance(result, ApplyError)
    assert result.error.code == "unknown_commander"
    assert _canon(before) == snapshot


def test_no_winner_is_set_only_when_the_eliminated_seat_was_the_last_one_standing() -> None:
    assert _run(_game(3), _cd(1, 0, 2, 21)).no_winner is False
    last = accept(active_game([1, 2, 3], 1, 1, out=(1, 3)), _cd(2, 0, 2, 21))
    assert last.no_winner is True
    assert _seat(last, 2).eliminated_at_seq == last.last_seq
