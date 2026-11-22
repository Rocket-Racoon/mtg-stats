import { MAX_SEATS } from './types';
import type { ApplyResult, EventEnvelope, GameState, ReducerErrorCode } from './types';

/*
 * Helpers shared by the handler modules (`lobby.ts`, `life.ts`, `commander-damage.ts`). Internal:
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
