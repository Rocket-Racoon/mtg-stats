import { MAX_SEATS } from './types';
import type { ApplyResult, EventEnvelope, GameState, ReducerErrorCode } from './types';

/*
 * Helpers shared by the handler modules (`lobby.ts`, `life.ts`, `commander-damage.ts`, `counters.ts`, `holders.ts`, `elimination.ts`). Internal:
 * not exported from `index.ts`. Pure: nothing here reads a clock, randomness or I/O (section 7).
 */

export type Handler = (state: GameState, event: EventEnvelope, seq: number) => ApplyResult;

export type Payload = Record<string, unknown>;

export const fail = (code: ReducerErrorCode): ApplyResult => ({ ok: false, error: { code } });

export function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

/** An integer of section 1: from -(2^53 - 1) to 2^53 - 1. */
export function isInt(v: unknown): v is number {
  return typeof v === 'number' && Number.isSafeInteger(v);
}

export function isSeatId(v: unknown): v is number {
  return isInt(v) && v >= 1 && v <= MAX_SEATS;
}

/** A payload value by key, never one inherited from the prototype. */
export function field(payload: Payload, key: string): unknown {
  return Object.hasOwn(payload, key) ? payload[key] : undefined;
}

/** An optional payload key that is given: neither absent nor `null`. */
export const isGiven = (v: unknown): boolean => v !== undefined && v !== null;

/** The phases in which the in-game handlers run (4.5.1). */
export const isRunning = (state: GameState): boolean =>
  state.phase === 'active' || state.phase === 'ending';

export const hasSeat = (state: GameState, seat: number): boolean =>
  state.seats.some((s) => s.seat === seat);

export const isEliminated = (state: GameState, seat: number): boolean =>
  state.seats.some((s) => s.seat === seat && s.eliminated_at_seq !== null);

export const payloadOf = (event: EventEnvelope): Payload => event.payload;

/**
 * The seat references of an in-game handler, in the order of 4.5.1: `unknown_seat` when a seat of
 * the list is not at the table, then `seat_eliminated` when one of them is eliminated. `null` when
 * every seat is at the table and in the game.
 */
export function seatRefsError(state: GameState, seats: readonly number[]): ReducerErrorCode | null {
  if (seats.some((seat) => !hasSeat(state, seat))) return 'unknown_seat';
  if (seats.some((seat) => isEliminated(state, seat))) return 'seat_eliminated';
  return null;
}

/**
 * The "next seat" walk of 4.4.10, on the state as it is: scan `order` cyclically after the position
 * of `active_seat` (from the head of `order` when it is `null` or not in `order`), up to and
 * including the first seat that is not eliminated; the active seat itself is scanned last.
 * `walked` lists the seats at the positions scanned, `next` is the survivor (`null` when every seat
 * is eliminated). Shared by `turn_passed` and by the monarch / initiative hand-off (4.5.2).
 */
export function walkToNextSeat(state: GameState): { next: number | null; walked: number[] } {
  const order = state.order;
  const at = order.indexOf(state.active_seat ?? -1);
  const walked: number[] = [];
  for (let step = 1; step <= order.length; step++) {
    const candidate = order[(at + step) % order.length]!;
    walked.push(candidate);
    if (!isEliminated(state, candidate)) return { next: candidate, walked };
  }
  return { next: null, walked };
}
