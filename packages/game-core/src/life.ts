import { eliminate } from './elimination';
import {
  fail,
  field,
  hasSeat,
  isEliminated,
  isGiven,
  isInt,
  isRunning,
  isSeatId,
  payloadOf,
} from './handler-utils';
import type { Handler } from './handler-utils';
import { WIN_CONDITIONS } from './types';
import type { GameState, WinCondition } from './types';

/*
 * Handlers of S-6.1.3 (CONTRACT.md section 4.5): life totals, concession and declared
 * eliminations. Pure: nothing here reads a clock, randomness or I/O (section 7).
 */

/** The largest |delta| of a `life_changed` (4.5.3). */
const MAX_LIFE_DELTA = 999;

export const lifeChanged: Handler = (state, event, seq) => {
  const payload = payloadOf(event);
  const seat = field(payload, 'seat');
  const delta = field(payload, 'delta');
  const source = field(payload, 'source_seat');
  if (!isSeatId(seat) || !isInt(delta)) return fail('invalid_payload');
  if (isGiven(source) && !isSeatId(source)) return fail('invalid_payload');
  if (!isRunning(state)) return fail('invalid_phase');
  if (!hasSeat(state, seat)) return fail('unknown_seat');
  if (isGiven(source) && !hasSeat(state, source as number)) return fail('unknown_seat');
  if (isEliminated(state, seat)) return fail('seat_eliminated');
  if (isGiven(source) && isEliminated(state, source as number)) return fail('seat_eliminated');
  if (delta === 0 || Math.abs(delta) > MAX_LIFE_DELTA) return fail('invalid_amount');
  const target = state.seats.find((s) => s.seat === seat)!;
  const current: unknown = target.life;
  if (!isInt(current) || !Number.isSafeInteger(current + delta)) return fail('invalid_amount');
  const life = current + delta;
  const changed: GameState = {
    ...state,
    seats: state.seats.map((s) => (s.seat === seat ? { ...s, life } : s)),
  };
  const next =
    delta < 0 && life <= 0
      ? eliminate(changed, seq, [
          { seat, reason: 'life', by: isGiven(source) ? (source as number) : null, cause: null },
        ])
      : changed;
  return { ok: true, state: { ...next, last_seq: seq } };
};

export const playerConceded: Handler = (state, event, seq) => {
  const seat = field(payloadOf(event), 'seat');
  if (!isSeatId(seat)) return fail('invalid_payload');
  if (!isRunning(state)) return fail('invalid_phase');
  if (!hasSeat(state, seat)) return fail('unknown_seat');
  if (isEliminated(state, seat)) return fail('seat_eliminated');
  const next = eliminate(state, seq, [{ seat, reason: 'concession', by: null, cause: null }]);
  return { ok: true, state: { ...next, last_seq: seq } };
};

export const playerEliminated: Handler = (state, event, seq) => {
  const payload = payloadOf(event);
  const seat = field(payload, 'seat');
  const cause = field(payload, 'cause');
  if (!isSeatId(seat)) return fail('invalid_payload');
  if (isGiven(cause) && !(WIN_CONDITIONS as readonly unknown[]).includes(cause)) {
    return fail('invalid_payload');
  }
  if (!isRunning(state)) return fail('invalid_phase');
  if (!hasSeat(state, seat)) return fail('unknown_seat');
  if (isEliminated(state, seat)) return fail('seat_eliminated');
  const next = eliminate(state, seq, [
    {
      seat,
      reason: 'declared',
      by: null,
      cause: isGiven(cause) ? (cause as WinCondition) : null,
    },
  ]);
  return { ok: true, state: { ...next, last_seq: seq } };
};
