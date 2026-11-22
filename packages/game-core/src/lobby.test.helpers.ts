import { apply } from './reducer';
import { initialState } from './state';
import { EVENT_TYPES } from './types';
import type { EventEnvelope, EventType, GameSettings, GameState } from './types';

/** Test helpers for the S-6.1.6 handlers (lobby, seats and turns). Not part of any bundle. */

export const GAME_ID = '018f0000-0000-7000-8000-000000000001';
export const HOST = '018f0000-0000-7000-8000-0000000000a1';

/** The nine event types whose handlers S-6.1.6 specifies (CONTRACT.md 4.4). */
export const S_6_1_6_TYPES = [
  'game_created',
  'settings_changed',
  'seat_joined',
  'seat_left',
  'seat_moved',
  'deck_assigned',
  'commanders_set',
  'game_started',
  'turn_passed',
] as const satisfies readonly EventType[];

/** The three event types whose handlers S-6.1.3 specifies (CONTRACT.md 4.5). */
export const S_6_1_3_TYPES = [
  'life_changed',
  'player_conceded',
  'player_eliminated',
] as const satisfies readonly EventType[];

/** The event type whose handler S-6.1.4 specifies (CONTRACT.md 4.6). */
export const S_6_1_4_TYPES = ['commander_damage'] as const satisfies readonly EventType[];

/** The five event types whose handlers S-6.1.5 specifies (CONTRACT.md 4.7). */
export const S_6_1_5_TYPES = [
  'poison_changed',
  'counter_changed',
  'commander_cast',
  'monarch_set',
  'initiative_set',
] as const satisfies readonly EventType[];

/** The types whose handlers are still no-ops. Shrinks as later stories land. */
export const NO_OP_TYPES: readonly EventType[] = EVENT_TYPES.filter(
  (t) =>
    !(S_6_1_6_TYPES as readonly string[]).includes(t) &&
    !(S_6_1_3_TYPES as readonly string[]).includes(t) &&
    !(S_6_1_4_TYPES as readonly string[]).includes(t) &&
    !(S_6_1_5_TYPES as readonly string[]).includes(t),
);

/** A type whose handler is a no-op in every phase, used to carry envelope tests. */
export const CARRIER_TYPE = 'seat_claimed';

let counter = 0;

/** An envelope with a fresh event id; `payload` and any envelope key can be overridden. */
export function make(
  type: string,
  payload: Record<string, unknown> = {},
  over: Record<string, unknown> = {},
): EventEnvelope {
  counter += 1;
  return {
    event_id: `00000000-0000-4000-8000-${String(counter).padStart(12, '0')}`,
    game_id: GAME_ID,
    type,
    payload,
    actor: { kind: 'player', player_id: HOST },
    on_behalf_of_seat: null,
    v: 1,
    ...over,
  } as unknown as EventEnvelope;
}

/** Apply events one after another and throw if one is rejected. */
export function run(state: GameState, ...events: EventEnvelope[]): GameState {
  let current = state;
  for (const e of events) {
    const r = apply(current, e);
    if (!r.ok) throw new Error(`${e.type} rejected with ${r.error.code}`);
    current = r.state;
  }
  return current;
}

export const joined = (seat: number, name = `Seat ${seat}`, player: string | null = null) =>
  make('seat_joined', { seat, player_id: player, name });

export const started = (first: number) =>
  make('game_started', { first_seat: first, started_at: '2026-11-21T12:00:00.000Z' });

export const moved = (from: number, to: number, correction?: boolean | null) =>
  make('seat_moved', correction === undefined ? { from, to } : { from, to, correction });

/** An empty lobby, with settings given or the defaults. */
export function lobbyState(settings: Partial<GameSettings> = {}): GameState {
  return initialState(GAME_ID, settings);
}

/** A lobby with seats 1 to `count` joined in order. */
export function seated(count: number, settings: Partial<GameSettings> = {}): GameState {
  return run(lobbyState(settings), ...Array.from({ length: count }, (_, i) => joined(i + 1)));
}

/** A started game with seats 1 to `count`, `first` active. */
export function activeGame(count: number, first = 1): GameState {
  return run(seated(count), started(first));
}

/** The error code of an event, or 'ok'. */
export function codeOf(state: GameState, e: EventEnvelope): string {
  const r = apply(state, e);
  return r.ok ? 'ok' : r.error.code;
}

/** The state a handler returns for an event, throwing on a rejection. */
export function after(state: GameState, e: EventEnvelope): GameState {
  return run(state, e);
}

/** A copy of a state with some seats marked eliminated, built by hand (a quick way to a state that events reach only after several steps). */
export function withEliminated(state: GameState, ...seats: number[]): GameState {
  return {
    ...state,
    seats: state.seats.map((s) =>
      seats.includes(s.seat)
        ? {
            ...s,
            elimination_reason: 'life',
            eliminated_by: null,
            eliminated_at_seq: state.last_seq,
            eliminated_round: state.round,
          }
        : s,
    ),
  };
}
