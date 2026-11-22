import { walkToNextSeat } from './handler-utils';
import type { EliminationReason, GameState, WinCondition } from './types';

/*
 * The elimination routine (CONTRACT.md 4.5.2). Every event that eliminates a seat calls it: the
 * life, concession and declared eliminations of S-6.1.3, commander damage (S-6.1.4), poison
 * (S-6.1.5) and later group events (S-6.1.7). Pure: nothing here reads a clock, randomness or I/O.
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
 * The new holder of the monarch or the initiative (4.5.2, "Monarch and initiative"). `holder` is
 * the current one, `applied` the seats this call eliminated, `after` the state with them
 * eliminated. A holder that this call did not eliminate keeps it. One that it did eliminate gives
 * it to the active seat when that seat is still standing, otherwise to the next seat of 4.4.10's
 * walk, and to nobody (`null`) when no seat is left.
 */
function handOff(after: GameState, holder: number | null, applied: ReadonlySet<number>) {
  if (holder === null || !applied.has(holder)) return holder;
  const active = after.seats.find(
    (s) => s.seat === after.active_seat && s.eliminated_at_seq === null,
  );
  if (active !== undefined) return active.seat;
  return walkToNextSeat(after).next;
}

/**
 * Eliminate the given seats at `seq`, all at once, apply the full-draw rule and hand off the
 * monarch and the initiative.
 *
 * - Entries naming a seat that is not at the table or is already eliminated are skipped, and a seat
 *   named twice takes its first entry. The callers check those cases first (`seat_eliminated`).
 * - Every applied seat gets the same `eliminated_at_seq` (`seq`) and `eliminated_round` (the state's
 *   `round`), plus its own reason, eliminator and cause.
 * - When the applied entries are every seat that was still in the game, `no_winner` becomes `true`.
 *   Otherwise `no_winner` is left as it is; nothing here sets it back to `false`.
 * - After that, a monarch or initiative holder among the applied seats passes to the active seat if
 *   it still stands, else to the next seat in turn order, else to `null` (nobody stands).
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
  const after: GameState = { ...state, seats, no_winner: fullDraw ? true : state.no_winner };
  const seatsOut: ReadonlySet<number> = new Set(applied.keys());
  return {
    ...after,
    monarch: handOff(after, state.monarch, seatsOut),
    initiative: handOff(after, state.initiative, seatsOut),
  };
}
