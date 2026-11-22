import {
  fail,
  field,
  hasSeat,
  isEliminated,
  isInt,
  isRecord,
  isSeatId,
  payloadOf,
} from './handler-utils';
import type { Handler } from './handler-utils';
import { MAX_SEATS, MIN_SEATS, SETTING_DEFAULTS, SETTING_RANGES, TRUST_MODES } from './types';
import type { ApplyResult, Commander, GameSettings, GameState, Seat } from './types';

/*
 * Handlers of S-6.1.6 (CONTRACT.md section 4.4): the lobby, the seats and the turn order.
 * Pure: nothing here reads a clock, randomness or I/O (section 7).
 */

const done = (state: GameState, seq: number, change: Partial<GameState>): ApplyResult => ({
  ok: true,
  state: { ...state, ...change, last_seq: seq },
});

function isNonEmptyString(v: unknown): v is string {
  return typeof v === 'string' && v !== '';
}

const SETTING_KEYS = Object.keys(SETTING_DEFAULTS) as (keyof GameSettings)[];

/** 4.4.1 "Settings values". */
function isValidSetting(key: keyof GameSettings, value: unknown): boolean {
  if (key === 'trust_mode') {
    return typeof value === 'string' && (TRUST_MODES as readonly string[]).includes(value);
  }
  const [min, max] = SETTING_RANGES[key];
  return isInt(value) && value >= min && value <= max;
}

/** 4.4.1 "Commanders": 1 or 2 items, each with `card_id` (non-empty string or null) and `name`. */
function parseCommanders(value: unknown): Commander[] | null {
  if (!Array.isArray(value) || value.length < 1 || value.length > 2) return null;
  const out: Commander[] = [];
  for (const item of value as unknown[]) {
    if (!isRecord(item)) return null;
    const cardId = field(item, 'card_id');
    const name = field(item, 'name');
    if (cardId !== null && !isNonEmptyString(cardId)) return null;
    if (typeof name !== 'string') return null;
    out.push({ card_id: cardId, name });
  }
  return out;
}

function newSeat(seat: number, playerId: string | null, name: string, life: number): Seat {
  return {
    seat,
    player_id: playerId,
    name,
    deck_version_id: null,
    commanders: [{ card_id: null, name: '' }],
    life,
    poison: 0,
    energy: 0,
    experience: 0,
    commander_cast: [0],
    elimination_reason: null,
    eliminated_by: null,
    eliminated_at_seq: null,
    eliminated_round: null,
    elimination_cause: null,
  };
}

export const gameCreated: Handler = (state, event, seq) => {
  const given = field(payloadOf(event), 'settings');
  if (!isRecord(given)) return fail('invalid_payload');
  if (SETTING_KEYS.some((key) => !Object.hasOwn(given, key))) return fail('invalid_payload');
  if (state.last_seq !== 0) return fail('invalid_phase');
  if (SETTING_KEYS.some((key) => !isValidSetting(key, given[key]))) return fail('invalid_setting');
  const settings = {
    starting_life: given.starting_life,
    commander_damage_threshold: given.commander_damage_threshold,
    poison_threshold: given.poison_threshold,
    trust_mode: given.trust_mode,
  } as GameSettings;
  return done(state, seq, { settings });
};

export const settingsChanged: Handler = (state, event, seq) => {
  if (state.phase !== 'lobby') return fail('invalid_phase');
  const payload = payloadOf(event);
  const given = SETTING_KEYS.filter((key) => Object.hasOwn(payload, key));
  if (given.some((key) => !isValidSetting(key, payload[key]))) return fail('invalid_setting');
  const settings = { ...state.settings } as Record<keyof GameSettings, unknown>;
  for (const key of given) settings[key] = payload[key];
  const next = settings as unknown as GameSettings;
  const seats = Object.hasOwn(payload, 'starting_life')
    ? state.seats.map((s) => ({ ...s, life: next.starting_life }))
    : state.seats;
  return done(state, seq, { settings: next, seats });
};

export const seatJoined: Handler = (state, event, seq) => {
  const payload = payloadOf(event);
  const seat = field(payload, 'seat');
  const playerId = field(payload, 'player_id');
  const name = field(payload, 'name');
  if (!isSeatId(seat)) return fail('invalid_payload');
  if (playerId !== null && !isNonEmptyString(playerId)) return fail('invalid_payload');
  if (typeof name !== 'string') return fail('invalid_payload');
  if (state.phase !== 'lobby') return fail('invalid_phase');
  if (state.seats.length >= MAX_SEATS) return fail('table_full');
  if (hasSeat(state, seat)) return fail('seat_taken');
  const seats = [...state.seats, newSeat(seat, playerId, name, state.settings.starting_life)].sort(
    (a, b) => a.seat - b.seat,
  );
  return done(state, seq, { seats, order: [...state.order, seat] });
};

export const seatLeft: Handler = (state, event, seq) => {
  const seat = field(payloadOf(event), 'seat');
  if (!isSeatId(seat)) return fail('invalid_payload');
  if (state.phase !== 'lobby') return fail('invalid_phase');
  if (!hasSeat(state, seat)) return fail('unknown_seat');
  return done(state, seq, {
    seats: state.seats.filter((s) => s.seat !== seat),
    order: state.order.filter((id) => id !== seat),
  });
};

export const seatMoved: Handler = (state, event, seq) => {
  const payload = payloadOf(event);
  const from = field(payload, 'from');
  const to = field(payload, 'to');
  const correction = field(payload, 'correction');
  if (!isSeatId(from) || !isSeatId(to)) return fail('invalid_payload');
  if (correction !== undefined && correction !== null && typeof correction !== 'boolean') {
    return fail('invalid_payload');
  }
  const corrects = state.phase === 'active' || state.phase === 'ending';
  if (state.phase !== 'lobby' && !(corrects && correction === true)) return fail('invalid_phase');
  if (!hasSeat(state, from) || !hasSeat(state, to)) return fail('unknown_seat');
  const order = [...state.order];
  const i = order.indexOf(from);
  const j = order.indexOf(to);
  order[i] = to;
  order[j] = from;
  return done(state, seq, { order });
};

function withCommanders(
  state: GameState,
  seat: number,
  commanders: Commander[],
  deckVersionId: string | null,
): Seat[] {
  return state.seats.map((s) => {
    if (s.seat !== seat) return s;
    return {
      ...s,
      deck_version_id: deckVersionId,
      commanders,
      commander_cast: commanders.map(() => 0),
    };
  });
}

export const deckAssigned: Handler = (state, event, seq) => {
  const payload = payloadOf(event);
  const seat = field(payload, 'seat');
  const deckVersionId = field(payload, 'deck_version_id');
  const commanders = parseCommanders(field(payload, 'commanders'));
  if (!isSeatId(seat) || !isNonEmptyString(deckVersionId) || commanders === null) {
    return fail('invalid_payload');
  }
  if (state.phase !== 'lobby') return fail('invalid_phase');
  if (!hasSeat(state, seat)) return fail('unknown_seat');
  return done(state, seq, { seats: withCommanders(state, seat, commanders, deckVersionId) });
};

export const commandersSet: Handler = (state, event, seq) => {
  const payload = payloadOf(event);
  const seat = field(payload, 'seat');
  const commanders = parseCommanders(field(payload, 'commanders'));
  if (!isSeatId(seat) || commanders === null) return fail('invalid_payload');
  if (state.phase !== 'lobby') return fail('invalid_phase');
  if (!hasSeat(state, seat)) return fail('unknown_seat');
  return done(state, seq, { seats: withCommanders(state, seat, commanders, null) });
};

export const gameStarted: Handler = (state, event, seq) => {
  const payload = payloadOf(event);
  const firstSeat = field(payload, 'first_seat');
  const startedAt = field(payload, 'started_at');
  if (!isSeatId(firstSeat) || typeof startedAt !== 'string') return fail('invalid_payload');
  if (state.phase !== 'lobby') return fail('invalid_phase');
  if (!hasSeat(state, firstSeat)) return fail('unknown_seat');
  if (state.seats.length < MIN_SEATS) return fail('not_enough_players');
  return done(state, seq, {
    phase: 'active',
    active_seat: firstSeat,
    first_seat: firstSeat,
    turn: 1,
    round: 1,
    started_at: startedAt,
    seats: state.seats.map((s) => ({ ...s, life: state.settings.starting_life })),
  });
};

export const turnPassed: Handler = (state, _event, seq) => {
  if (state.phase !== 'active' && state.phase !== 'ending') return fail('invalid_phase');
  const order = state.order;
  const at = order.indexOf(state.active_seat ?? -1);
  // Walk the positions after the active seat, cyclically, up to and including the next survivor
  // (the active seat itself is walked last). 4.4.10.
  const walked: number[] = [];
  let next: number | null = null;
  for (let step = 1; step <= order.length; step++) {
    const candidate = order[(at + step) % order.length]!;
    walked.push(candidate);
    if (!isEliminated(state, candidate)) {
      next = candidate;
      break;
    }
  }
  if (next === null) return fail('invalid_phase');
  const firstWalked = state.first_seat !== null && walked.includes(state.first_seat);
  return done(state, seq, {
    active_seat: next,
    turn: state.turn + 1,
    round: firstWalked ? state.round + 1 : state.round,
  });
};
