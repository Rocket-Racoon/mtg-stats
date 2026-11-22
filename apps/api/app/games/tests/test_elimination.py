"""The elimination routine (CONTRACT.md 4.5.2) called directly, twin of ``elimination.test.ts``.

No event eliminates several seats at once before S-6.1.7 (``group_drain``), so criterion 6 of
S-6.1.3 and the "every seat still in the game at once" half of criterion 7 have no shared vector
yet: they are pinned here, and S-6.1.7 adds the vectors.
"""

from __future__ import annotations

from typing import Any

from app.games.core import GameState, canonical_json
from app.games.core.contract import EliminationReason, Seat, WinCondition
from app.games.core.elimination import Elimination, eliminate
from app.games.tests.lobby_helpers import accept, active_game, make_event


def _life(seat: int, reason: EliminationReason = "life") -> Elimination:
    return Elimination(seat, reason, None, None)


def _game() -> GameState:
    """Round 2, last_seq 19 (so the next event is seq 20), seats 1 to 4 in the game."""
    return active_game([1, 2, 3, 4], 1, 1, round_=2).model_copy(update={"last_seq": 19})


def _seat(state: GameState, seat_id: int) -> Seat:
    return next(s for s in state.seats if s.seat == seat_id)


def _fields(state: GameState, seat_id: int) -> tuple[Any, ...]:
    s = _seat(state, seat_id)
    return (
        s.elimination_reason,
        s.eliminated_by,
        s.eliminated_at_seq,
        s.eliminated_round,
        s.elimination_cause,
    )


def _seqs(state: GameState) -> list[int | None]:
    return [s.eliminated_at_seq for s in state.seats]


# --- several seats at once (criterion 6) ---------------------------------------------------------


def test_every_applied_seat_gets_the_same_seq_and_round_and_its_own_reason_by_and_cause() -> None:
    cause: WinCondition = "mill"
    entries = [
        Elimination(2, "life", 1, None),
        Elimination(3, "poison", None, None),
        Elimination(4, "declared", None, cause),
    ]
    after = eliminate(_game(), 20, entries)
    assert _fields(after, 2) == ("life", 1, 20, 2, None)
    assert _fields(after, 3) == ("poison", None, 20, 2, None)
    assert _fields(after, 4) == ("declared", None, 20, 2, "mill")
    assert _fields(after, 1) == (None, None, None, None, None)
    assert after.no_winner is False


def test_the_result_does_not_depend_on_the_order_of_the_entries() -> None:
    a = eliminate(_game(), 20, [_life(2), _life(4)])
    b = eliminate(_game(), 20, [_life(4), _life(2)])
    assert canonical_json(a.to_json_value()) == canonical_json(b.to_json_value())


def test_only_the_seats_and_no_winner_can_change_and_the_seats_stay_sorted() -> None:
    before = _game()
    after = eliminate(before, 20, [_life(3), _life(1)])
    assert [s.seat for s in after.seats] == [1, 2, 3, 4]
    restored = after.model_copy(update={"seats": before.seats, "no_winner": before.no_winner})
    assert restored == before


# --- the full draw (criterion 7) -----------------------------------------------------------------


def test_no_winner_is_set_when_the_entries_are_every_seat_still_in_the_game() -> None:
    after = eliminate(_game(), 20, [_life(1), _life(2), _life(3), _life(4)])
    assert after.no_winner is True
    assert _seqs(after) == [20, 20, 20, 20]
    assert after.winner_seat is None
    assert after.phase == "active"


def test_earlier_eliminations_stay_losses_and_only_the_seats_still_in_count() -> None:
    before = accept(_game(), make_event("player_conceded", {"seat": 1}))
    after = eliminate(before, 21, [_life(2), _life(3), _life(4)])
    assert after.no_winner is True
    assert _seqs(after) == [20, 21, 21, 21]
    assert [s.elimination_reason for s in after.seats] == ["concession", "life", "life", "life"]


def test_no_winner_is_not_set_while_one_seat_is_left() -> None:
    assert eliminate(_game(), 20, [_life(1), _life(2), _life(3)]).no_winner is False


def test_no_winner_is_set_when_the_one_seat_left_is_eliminated_the_literal_rule() -> None:
    before = eliminate(_game(), 20, [_life(1), _life(2), _life(3)])
    after = eliminate(before, 21, [_life(4)])
    assert after.no_winner is True
    assert _seqs(after) == [20, 20, 20, 21]


def test_no_winner_is_never_set_back_to_false() -> None:
    drawn = eliminate(_game(), 20, [_life(1), _life(2), _life(3), _life(4)])
    assert eliminate(drawn, 21, [_life(1)]).no_winner is True
    flagged = _game().model_copy(update={"no_winner": True})
    assert eliminate(flagged, 21, [_life(1)]).no_winner is True


# --- entries it skips ----------------------------------------------------------------------------


def test_an_empty_list_returns_the_same_state() -> None:
    before = _game()
    assert eliminate(before, 20, []) is before


def test_a_seat_not_at_the_table_is_skipped() -> None:
    before = _game()
    assert eliminate(before, 20, [_life(7)]) is before
    after = eliminate(before, 20, [_life(7), _life(2)])
    assert _seqs(after) == [None, 20, None, None]


def test_a_seat_already_eliminated_is_skipped_and_keeps_its_fields() -> None:
    once = eliminate(_game(), 20, [Elimination(2, "life", 1, None)])
    twice = eliminate(once, 21, [_life(2, "concession"), _life(3)])
    assert _fields(twice, 2) == ("life", 1, 20, 2, None)
    assert _fields(twice, 3) == ("life", None, 21, 2, None)


def test_a_seat_named_twice_takes_its_first_entry() -> None:
    after = eliminate(_game(), 20, [_life(2, "poison"), _life(2, "life")])
    assert _fields(after, 2) == ("poison", None, 20, 2, None)


def test_a_seat_named_twice_does_not_count_twice_towards_the_full_draw() -> None:
    after = eliminate(_game(), 20, [_life(1), _life(1), _life(2), _life(3)])
    assert after.no_winner is False
    assert _seqs(after) == [20, 20, 20, None]


def test_no_winner_is_not_set_when_nothing_was_applied_even_with_no_seat_in_the_game() -> None:
    out = eliminate(_game(), 20, [_life(1), _life(2), _life(3), _life(4)])
    assert eliminate(out, 21, [_life(1)]) is out
    cleared = out.model_copy(update={"no_winner": False})
    assert eliminate(cleared, 21, [_life(1)]).no_winner is False


def test_a_table_with_no_seats_is_returned_as_it_is() -> None:
    empty = _game().model_copy(update={"seats": [], "order": []})
    assert eliminate(empty, 20, [_life(1)]) is empty


# --- purity --------------------------------------------------------------------------------------


def test_the_input_state_is_not_mutated_and_last_seq_is_left_to_the_caller() -> None:
    before = _game()
    snapshot = canonical_json(before.to_json_value())
    after = eliminate(before, 20, [_life(1), _life(2)])
    assert canonical_json(before.to_json_value()) == snapshot
    assert after.last_seq == 19
    assert all(s.eliminated_at_seq is None for s in before.seats)


def test_the_entries_list_is_not_mutated() -> None:
    entries = [_life(2), _life(2, "poison")]
    eliminate(_game(), 20, entries)
    assert entries == [_life(2), _life(2, "poison")]
