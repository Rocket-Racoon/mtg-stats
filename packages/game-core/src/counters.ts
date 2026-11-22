import { eliminate } from './elimination';
import {
  fail,
  field,
  isGiven,
  isInt,
  isRunning,
  isSeatId,
  payloadOf,
  seatRefsError,
} from './handler-utils';
import type { Handler } from './handler-utils';
import { COUNTERS } from './types';
import type { CounterName, GameState } from './types';

/*
 * Handlers of S-6.1.5 (CONTRACT.md 4.7.1 to 4.7.3): poison, the energy and experience counters and
 * the commander cast counts. Pure: nothing here reads a clock, randomness or I/O (section 7).
 */

/** The largest |delta| of a `poison_changed` and of a `counter_changed` (4.7), as for life (4.5.3). */
const MAX_DELTA = 999;

const isCounter = (v: unknown): v is CounterName => (COUNTERS as readonly unknown[]).includes(v);

/** A counter value after a change: from 0 to 2^53 - 1, else `null` (4.7). */
const counted = (current: unknown, delta: number): number | null => {
  if (!isInt(current)) return null;
  const next = current + delta;
  return next >= 0 && Number.isSafeInteger(next) ? next : null;
};

export const poisonChanged: Handler = (state, event, seq) => {
  const payload = payloadOf(event);
  const seat = field(payload, 'seat');
  const delta = field(payload, 'delta');
  const source = field(payload, 'source_seat');
  if (!isSeatId(seat) || !isInt(delta)) return fail('invalid_payload');
  if (isGiven(source) && !isSeatId(source)) return fail('invalid_payload');
  if (!isRunning(state)) return fail('invalid_phase');
  const refs = seatRefsError(state, isGiven(source) ? [seat, source as number] : [seat]);
  if (refs !== null) return fail(refs);
  if (delta === 0 || Math.abs(delta) > MAX_DELTA) return fail('invalid_amount');
  const target = state.seats.find((s) => s.seat === seat)!;
  const threshold: unknown = state.settings.poison_threshold;
  const poison = counted(target.poison, delta);
  if (poison === null || !isInt(threshold)) return fail('invalid_amount');
  const changed: GameState = {
    ...state,
    seats: state.seats.map((s) => (s.seat === seat ? { ...s, poison } : s)),
  };
  // 4.5.2 precedence: only what this event raised counts, so a removal never eliminates.
  const next =
    delta > 0 && poison >= threshold
      ? eliminate(changed, seq, [
          {
            seat,
            reason: 'poison',
            by: isGiven(source) ? (source as number) : null,
            cause: null,
          },
        ])
      : changed;
  return { ok: true, state: { ...next, last_seq: seq } };
};

export const counterChanged: Handler = (state, event, seq) => {
  const payload = payloadOf(event);
  const seat = field(payload, 'seat');
  const counter = field(payload, 'counter');
  const delta = field(payload, 'delta');
  if (!isSeatId(seat) || !isCounter(counter) || !isInt(delta)) return fail('invalid_payload');
  if (!isRunning(state)) return fail('invalid_phase');
  const refs = seatRefsError(state, [seat]);
  if (refs !== null) return fail(refs);
  if (delta === 0 || Math.abs(delta) > MAX_DELTA) return fail('invalid_amount');
  const target = state.seats.find((s) => s.seat === seat)!;
  const value = counted(target[counter], delta);
  if (value === null) return fail('invalid_amount');
  return {
    ok: true,
    state: {
      ...state,
      seats: state.seats.map((s) => (s.seat === seat ? { ...s, [counter]: value } : s)),
      last_seq: seq,
    },
  };
};

export const commanderCast: Handler = (state, event, seq) => {
  const payload = payloadOf(event);
  const seat = field(payload, 'seat');
  const commander = field(payload, 'commander');
  const delta = field(payload, 'delta');
  if (!isSeatId(seat) || !isInt(commander) || !isInt(delta)) return fail('invalid_payload');
  if (!isRunning(state)) return fail('invalid_phase');
  const refs = seatRefsError(state, [seat]);
  if (refs !== null) return fail(refs);
  if (delta !== 1 && delta !== -1) return fail('invalid_amount');
  const target = state.seats.find((s) => s.seat === seat)!;
  if (commander < 0 || commander >= target.commanders.length) return fail('unknown_commander');
  const casts: unknown = target.commander_cast;
  const count = counted(Array.isArray(casts) ? (casts[commander] as unknown) : undefined, delta);
  if (count === null) return fail('invalid_amount');
  const commander_cast = target.commander_cast.map((n, i) => (i === commander ? count : n));
  return {
    ok: true,
    state: {
      ...state,
      seats: state.seats.map((s) => (s.seat === seat ? { ...s, commander_cast } : s)),
      last_seq: seq,
    },
  };
};
