import { eliminate } from './elimination';
import type { Elimination } from './elimination';
import {
  fail,
  field,
  hasSeat,
  isEliminated,
  isInt,
  isRunning,
  isSeatId,
  payloadOf,
} from './handler-utils';
import type { Handler } from './handler-utils';

/*
 * Handler of S-6.1.4 (CONTRACT.md section 4.6): commander damage per commander source. Pure:
 * nothing here reads a clock, randomness or I/O (section 7).
 */

/** The largest `amount` of a `commander_damage` (4.6). */
const MAX_AMOUNT = 999;

export const commanderDamage: Handler = (state, event, seq) => {
  const payload = payloadOf(event);
  const source = field(payload, 'source_seat');
  const commander = field(payload, 'commander');
  const target = field(payload, 'target_seat');
  const amount = field(payload, 'amount');
  if (!isSeatId(source) || !isSeatId(target) || !isInt(commander) || !isInt(amount)) {
    return fail('invalid_payload');
  }
  if (!isRunning(state)) return fail('invalid_phase');
  if (!hasSeat(state, source) || !hasSeat(state, target)) return fail('unknown_seat');
  if (isEliminated(state, target) || isEliminated(state, source)) return fail('seat_eliminated');
  if (amount < 1 || amount > MAX_AMOUNT) return fail('invalid_amount');
  const sourceSeat = state.seats.find((s) => s.seat === source)!;
  if (commander < 0 || commander >= sourceSeat.commanders.length) return fail('unknown_commander');
  const targetSeat = state.seats.find((s) => s.seat === target)!;
  const life: unknown = targetSeat.life;
  const threshold: unknown = state.settings.commander_damage_threshold;
  if (!isInt(life) || !isInt(threshold)) return fail('invalid_amount');
  const targetKey = String(target);
  const sourceKey = `${source}:${commander}`;
  const row = Object.hasOwn(state.commander_damage, targetKey)
    ? state.commander_damage[targetKey]!
    : {};
  const before = Object.hasOwn(row, sourceKey) ? row[sourceKey]! : 0;
  if (!Number.isSafeInteger(before + amount) || !Number.isSafeInteger(life - amount)) {
    return fail('invalid_amount');
  }
  const tally = before + amount;
  const newLife = life - amount;
  const changed = {
    ...state,
    seats: state.seats.map((s) => (s.seat === target ? { ...s, life: newLife } : s)),
    commander_damage: {
      ...state.commander_damage,
      [targetKey]: { ...row, [sourceKey]: tally },
    },
  };
  // 4.5.2 precedence: commander_damage, then (poison, not touched here), then life.
  let entry: Elimination | null = null;
  if (tally >= threshold) {
    entry = { seat: target, reason: 'commander_damage', by: source, cause: null };
  } else if (newLife <= 0) {
    entry = { seat: target, reason: 'life', by: source, cause: null };
  }
  const next = entry === null ? changed : eliminate(changed, seq, [entry]);
  return { ok: true, state: { ...next, last_seq: seq } };
};
