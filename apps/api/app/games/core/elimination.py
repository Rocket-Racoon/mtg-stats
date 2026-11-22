"""The elimination routine (CONTRACT.md section 4.5.2).

Every event that eliminates seats calls :func:`eliminate`: the life, concession and declared
eliminations of S-6.1.3, commander damage (S-6.1.4), poison (S-6.1.5) and later group
events (S-6.1.7). No handler writes the elimination fields itself. The routine also hands the
monarch and the initiative on when their holder is eliminated (step 4 of 4.5.2).

Pure: no clock, randomness or I/O. The input state is never mutated.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple

from app.games.core.contract import EliminationReason, GameState, WinCondition


class Elimination(NamedTuple):
    """One seat to eliminate, with the elimination fields it gets (3.3), seq and round aside."""

    seat: int
    reason: EliminationReason
    by: int | None
    """The seat that caused the elimination, or ``None``."""
    cause: WinCondition | None
    """The D5 code of a host ``player_eliminated``, else ``None``."""


def next_standing_seat(state: GameState, start: int | None) -> int | None:
    """The "next seat" of CONTRACT.md 4.4.10, shared by ``turn_passed`` and the hand-off.

    Scan ``order`` cyclically after the position of ``start`` (``start`` itself last) and return
    the first seat that is not eliminated. A ``start`` that is ``None`` or not in ``order`` begins
    the scan at the head of ``order``. Returns ``None`` when no seat qualifies.
    """
    order = state.order
    position = order.index(start) if start in order else -1
    eliminated = {s.seat for s in state.seats if s.eliminated_at_seq is not None}
    for candidate in [*order[position + 1 :], *order[: position + 1]]:
        if candidate not in eliminated:
            return candidate
    return None


def _hand_off(
    state: GameState, holder: int | None, applied: set[int], survivors: set[int]
) -> int | None:
    """Step 4 of 4.5.2 for one key (``monarch`` or ``initiative``): who holds it afterwards."""
    if holder is None or holder not in applied:
        return holder
    if not survivors:
        return None
    if state.active_seat is not None and state.active_seat in survivors:
        return state.active_seat
    return next_standing_seat(state, state.active_seat)


def _fields(entry: Elimination, seq: int, round_: int) -> dict[str, object]:
    return {
        "elimination_reason": entry.reason,
        "eliminated_by": entry.by,
        "eliminated_at_seq": seq,
        "eliminated_round": round_,
        "elimination_cause": entry.cause,
    }


def eliminate(state: GameState, seq: int, eliminations: Sequence[Elimination]) -> GameState:
    """Eliminate the given seats at ``seq``, all at once, and apply the full-draw rule.

    - An entry is skipped (no effect, no error) when its seat is not standing (not at the table,
      or eliminated before this call) or when an earlier entry names the same seat.
    - Every applied seat gets the same ``eliminated_at_seq`` (``seq``) and ``eliminated_round``
      (the state's ``round``), plus its own reason, eliminator and cause.
    - ``no_winner`` becomes ``True`` iff at least one entry was applied and the applied seats are
      every seat that was standing. In every other case it is left as it is.

    - The monarch and the initiative (step 4) are judged independently: a key whose holder this
      call eliminated becomes ``None`` when nobody is left standing, else the active seat when it
      is standing, else the next standing seat of ``order`` (:func:`next_standing_seat`). Any
      other key is left as it is.

    Nothing else is touched (not ``last_seq``, ``phase``, ``round``, ``winner_seat``...). Returns
    a new state, or ``state`` itself when no entry was applied.
    """
    standing = {seat.seat for seat in state.seats if seat.eliminated_at_seq is None}
    applied: dict[int, Elimination] = {}
    for entry in eliminations:
        if entry.seat in standing and entry.seat not in applied:
            applied[entry.seat] = entry
    if not applied:
        return state
    seats = [
        seat.model_copy(update=_fields(applied[seat.seat], seq, state.round))
        if seat.seat in applied
        else seat
        for seat in state.seats
    ]
    full_draw = len(applied) == len(standing)
    after = state.model_copy(
        update={"seats": seats, "no_winner": True if full_draw else state.no_winner}
    )
    survivors = standing - set(applied)
    return after.model_copy(
        update={
            "monarch": _hand_off(after, state.monarch, set(applied), survivors),
            "initiative": _hand_off(after, state.initiative, set(applied), survivors),
        }
    )
