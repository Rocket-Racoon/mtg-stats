import type { EliminationReason, GameState, WinCondition } from './types';

/*
 * The elimination routine (CONTRACT.md 4.5.2). Every event that eliminates a seat calls it: the
 * life, concession and declared eliminations of S-6.1.3, commander damage (S-6.1.4), and later
 * poison (S-6.1.5) and group events (S-6.1.7). Pure: nothing here reads a clock, randomness or I/O.
 */

/** One seat to eliminate, with the five elimination fields it gets (3.3), `at_seq` and round aside. */
export interface Elimination {
  seat: number;
  reason: EliminationReason;
  /** Seat that caused the elimination, or null. */
  by: number | null;
  /** The D5 code of a host `player_eliminated`, else null. */
  cause: WinCondition | null;
}

/**
 * Eliminate the given seats at `seq`, all at once, and apply the full-draw rule.
 *
 * - Entries naming a seat that is not at the table or is already eliminated are skipped, and a seat
 *   named twice takes its first entry. The callers check those cases first (`seat_eliminated`).
 * - Every applied seat gets the same `eliminated_at_seq` (`seq`) and `eliminated_round` (the state's
 *   `round`), plus its own reason, eliminator and cause.
 * - When the applied entries are every seat that was still in the game, `no_winner` becomes `true`.
 *   Otherwise `no_winner` is left as it is; nothing here sets it back to `false`.
 *
 * Returns a new state. `last_seq` and every other key are left to the caller.
 */
export function eliminate(
  state: GameState,
  seq: number,
  eliminations: readonly Elimination[],
): GameState {
  const standing = new Set(
    state.seats.filter((s) => s.eliminated_at_seq === null).map((s) => s.seat),
  );
  const applied = new Map<number, Elimination>();
  for (const entry of eliminations) {
    if (standing.has(entry.seat) && !applied.has(entry.seat)) applied.set(entry.seat, entry);
  }
  if (applied.size === 0) return state;
  const seats = state.seats.map((s) => {
    const entry = applied.get(s.seat);
    if (entry === undefined) return s;
    return {
      ...s,
      elimination_reason: entry.reason,
      eliminated_by: entry.by,
      eliminated_at_seq: seq,
      eliminated_round: state.round,
      elimination_cause: entry.cause,
    };
  });
  const fullDraw = applied.size === standing.size;
  return { ...state, seats, no_winner: fullDraw ? true : state.no_winner };
}
