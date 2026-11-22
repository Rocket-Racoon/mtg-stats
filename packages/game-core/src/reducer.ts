import { CanonicalJsonError, canonicalJson } from './canonical-json';
import { commanderDamage } from './commander-damage';
import { commanderCast, counterChanged, poisonChanged } from './counters';
import type { Handler } from './handler-utils';
import { initiativeSet, monarchSet } from './holders';
import { lifeChanged, playerConceded, playerEliminated } from './life';
import {
  commandersSet,
  deckAssigned,
  gameCreated,
  gameStarted,
  seatJoined,
  seatLeft,
  seatMoved,
  settingsChanged,
  turnPassed,
} from './lobby';
import { hasLoneSurrogate } from './unicode';
import { EVENT_TYPES, MAX_SEATS, SUPPORTED_VERSIONS } from './types';
import type {
  ApplyResult,
  EventEnvelope,
  EventType,
  GameState,
  ReducerError,
  ReducerErrorCode,
} from './types';

/*
 * Purity rule (CONTRACT.md section 7): nothing in this file, or any other non-test file of
 * this package, may read a clock, randomness or I/O. `purity.test.ts` enforces it.
 */

const fail = (code: ReducerErrorCode): { ok: false; error: ReducerError } => ({
  ok: false,
  error: { code },
});

/** Handlers not yet specified record the event and change nothing else; later stories replace them one by one. */
const noop: Handler = (state, _event, seq) => ({ ok: true, state: { ...state, last_seq: seq } });

const HANDLERS: Readonly<Record<EventType, Handler>> = {
  ...(Object.fromEntries(EVENT_TYPES.map((type) => [type, noop])) as Record<EventType, Handler>),
  game_created: gameCreated,
  settings_changed: settingsChanged,
  seat_joined: seatJoined,
  seat_left: seatLeft,
  seat_moved: seatMoved,
  deck_assigned: deckAssigned,
  commanders_set: commandersSet,
  game_started: gameStarted,
  turn_passed: turnPassed,
  life_changed: lifeChanged,
  commander_damage: commanderDamage,
  poison_changed: poisonChanged,
  counter_changed: counterChanged,
  commander_cast: commanderCast,
  monarch_set: monarchSet,
  initiative_set: initiativeSet,
  player_conceded: playerConceded,
  player_eliminated: playerEliminated,
};

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function isInt(v: unknown): v is number {
  return typeof v === 'number' && Number.isSafeInteger(v);
}

const ENVELOPE_KEYS: ReadonlySet<string> = new Set([
  'event_id',
  'game_id',
  'seq',
  'type',
  'payload',
  'actor',
  'on_behalf_of_seat',
  'ts',
  'v',
]);

function isActorValid(actor: unknown): boolean {
  if (!isRecord(actor)) return false;
  const keys = Object.keys(actor);
  if (keys.length !== 2 || !('kind' in actor) || !('player_id' in actor)) return false;
  if (actor.kind === 'player') {
    return (
      typeof actor.player_id === 'string' &&
      actor.player_id !== '' &&
      !hasLoneSurrogate(actor.player_id)
    );
  }
  return actor.kind === 'system' && actor.player_id === null;
}

function isCanonicalValue(value: unknown): boolean {
  try {
    canonicalJson(value);
    return true;
  } catch (e) {
    if (e instanceof CanonicalJsonError) return false;
    throw e;
  }
}

function isEnvelopeValid(e: Record<string, unknown>): boolean {
  if (Object.keys(e).some((key) => !ENVELOPE_KEYS.has(key))) return false;
  if (typeof e.event_id !== 'string' || !UUID_V4.test(e.event_id)) return false;
  if (typeof e.game_id !== 'string' || e.game_id === '' || hasLoneSurrogate(e.game_id)) {
    return false;
  }
  if (e.seq !== undefined && (!isInt(e.seq) || e.seq < 1)) return false;
  if (e.ts !== undefined && (typeof e.ts !== 'string' || hasLoneSurrogate(e.ts))) return false;
  if (!isRecord(e.payload)) return false;
  if (!isActorValid(e.actor)) return false;
  const seat = e.on_behalf_of_seat;
  return seat === null || (isInt(seat) && seat >= 1 && seat <= MAX_SEATS);
}

function isEventType(type: string): type is EventType {
  return (EVENT_TYPES as readonly string[]).includes(type);
}

/**
 * Apply one event to a state. Pure: never mutates `state` or `event`, never reads a clock,
 * randomness or I/O. A rejected event returns an error and the caller keeps the old state.
 *
 * Check order (CONTRACT.md section 6.2): `v`, `type`, envelope shape, payload is canonical,
 * `game_id`, `seq`, handler.
 * An event without `seq` is an optimistic (not yet acked) event and is applied as `last_seq + 1`.
 */
export function apply(state: GameState, event: EventEnvelope): ApplyResult {
  const raw: unknown = event;
  if (!isRecord(raw) || !isInt(raw.v)) return fail('invalid_event');
  if (!SUPPORTED_VERSIONS.includes(raw.v)) return fail('unsupported_version');
  if (typeof raw.type !== 'string') return fail('invalid_event');
  if (!isEventType(raw.type)) return fail('unknown_event_type');
  if (!isEnvelopeValid(raw)) return fail('invalid_event');
  if (!isCanonicalValue(raw.payload)) return fail('invalid_payload');
  if (raw.game_id !== state.game_id) return fail('game_mismatch');
  const expected = state.last_seq + 1;
  const seq = typeof raw.seq === 'number' ? raw.seq : expected;
  if (seq !== expected) return fail('out_of_order');
  return HANDLERS[raw.type](state, event, seq);
}

/** Alias that matches the name used in docs/architecture.md. */
export const reduce = apply;

export type ReplayResult =
  { ok: true; state: GameState } | { ok: false; index: number; error: ReducerError };

/** Fold events over a state; stops at the first rejected event and reports its index. */
export function replay(initial: GameState, events: readonly EventEnvelope[]): ReplayResult {
  let state = initial;
  for (let index = 0; index < events.length; index++) {
    const result = apply(state, events[index]!);
    if (!result.ok) return { ok: false, index, error: result.error };
    state = result.state;
  }
  return { ok: true, state };
}
