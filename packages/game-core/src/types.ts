/** Types for the game state, the event envelope, the event registry and the error codes. See CONTRACT.md. */

/** Schema version of the envelope and of the state. Bumped on any breaking contract change. */
export const SCHEMA_VERSION = 1;
export const SUPPORTED_VERSIONS: readonly number[] = [SCHEMA_VERSION];

export const MIN_SEATS = 2;
export const MAX_SEATS = 8;

export const PHASES = ['lobby', 'active', 'ending', 'finished', 'abandoned'] as const;
export type Phase = (typeof PHASES)[number];

/** The phases a game can be resumed into (`game_resumed` never returns to a closed game). */
export type ResumePhase = Exclude<Phase, 'finished' | 'abandoned'>;

export const TRUST_MODES = ['casual', 'strict'] as const;
export type TrustMode = (typeof TRUST_MODES)[number];

export const CONTROL_MODES = ['own_seat', 'host_all'] as const;
export type ControlMode = (typeof CONTROL_MODES)[number];

/** D5 win-condition codes. There is deliberately no "draw": a draw is `no_winner`. */
export const WIN_CONDITIONS = [
  'combat_damage',
  'commander_damage',
  'combo',
  'alternate_win',
  'poison',
  'mill',
  'life_drain',
  'concession',
  'last_standing',
  'other',
] as const;
export type WinCondition = (typeof WIN_CONDITIONS)[number];

export const ELIMINATION_REASONS = [
  'life',
  'commander_damage',
  'poison',
  'concession',
  'declared',
] as const;
export type EliminationReason = (typeof ELIMINATION_REASONS)[number];

export const COUNTERS = ['energy', 'experience'] as const;
export type CounterName = (typeof COUNTERS)[number];

export const VALUE_SET_FIELDS = [
  'life',
  'poison',
  'energy',
  'experience',
  'commander_damage',
  'commander_cast',
] as const;
export type ValueSetField = (typeof VALUE_SET_FIELDS)[number];

export const ABANDON_REASONS = ['host', 'idle'] as const;
export type AbandonReason = (typeof ABANDON_REASONS)[number];

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

export interface GameSettings {
  starting_life: number;
  commander_damage_threshold: number;
  poison_threshold: number;
  trust_mode: TrustMode;
}

export const SETTING_DEFAULTS: GameSettings = {
  starting_life: 40,
  commander_damage_threshold: 21,
  poison_threshold: 10,
  trust_mode: 'casual',
};

/** Inclusive ranges for the numeric settings (S-6.1.3, S-6.1.4, S-6.1.5). */
export const SETTING_RANGES = {
  starting_life: [1, 999],
  commander_damage_threshold: [1, 99],
  poison_threshold: [1, 99],
} as const;

export interface Commander {
  /** Card id from the cards domain, or null for the unnamed default commander. */
  card_id: string | null;
  /** Display name, "" for the unnamed default commander. */
  name: string;
}

export interface Seat {
  /** Stable seat id, 1..8. Never changes when seats are reordered. */
  seat: number;
  player_id: string | null;
  name: string;
  deck_version_id: string | null;
  /** 1 or 2 commanders. */
  commanders: Commander[];
  life: number;
  poison: number;
  energy: number;
  experience: number;
  /** Cast count per commander, same length and order as `commanders`. */
  commander_cast: number[];
  /** Why the seat was eliminated; null while in the game. The seat is eliminated iff this is non-null. */
  elimination_reason: EliminationReason | null;
  /** Seat that caused the elimination, or null (S-6.1.3). */
  eliminated_by: number | null;
  /** `seq` of the event that eliminated the seat; equal values mean "eliminated at the same time". */
  eliminated_at_seq: number | null;
  /** The game round when the seat was eliminated (S-6.7.2, S-6.7.4). */
  eliminated_round: number | null;
  /** D5 code given with a host `player_eliminated`, else null. */
  elimination_cause: WinCondition | null;
}

export interface GameState {
  v: number;
  game_id: string;
  phase: Phase;
  /** The phase to return to on `game_resumed`; non-null only while `abandoned`. */
  resume_phase: ResumePhase | null;
  settings: GameSettings;
  control_mode: ControlMode;
  /** Seats sorted by ascending `seat`. */
  seats: Seat[];
  /** Seat ids in turn order. */
  order: number[];
  active_seat: number | null;
  /** Seat that took the first turn: null until `game_started`, never changed afterwards. */
  first_seat: number | null;
  /** Individual turn counter, 0 before `game_started`. */
  turn: number;
  /** The game's "turn count" for results (D4), 0 before `game_started`. */
  round: number;
  monarch: number | null;
  initiative: number | null;
  /** `commander_damage[target seat as decimal string][source_key]`, source_key = "<seat>:<commander index>". */
  commander_damage: Record<string, Record<string, number>>;
  no_winner: boolean;
  winner_seat: number | null;
  win_condition: WinCondition | null;
  win_condition_note: string;
  /** `started_at` from the `game_started` payload, else null. */
  started_at: string | null;
  /** Seqs skipped when state was rebuilt after a revert (S-6.6.1), ascending. */
  voided: number[];
  /** Seqs of events currently reverted by an effective `event_reverted` (S-6.6.1), ascending, no duplicates. */
  reverted: number[];
  /** `seq` of the last applied event, 0 for none. */
  last_seq: number;
}

// ---------------------------------------------------------------------------
// Envelope
// ---------------------------------------------------------------------------

export interface Actor {
  kind: 'player' | 'system';
  /** Players-domain id; null for `system`. */
  player_id: string | null;
}

export interface EventEnvelope<T extends string = string, P = Record<string, unknown>> {
  event_id: string;
  game_id: string;
  /** Server-assigned; absent until the event is acked or broadcast. */
  seq?: number;
  type: T;
  payload: P;
  actor: Actor;
  on_behalf_of_seat: number | null;
  /** Server UTC time, `YYYY-MM-DDTHH:MM:SS.mmmZ`; absent until acked. Reducers never read it. */
  ts?: string;
  v: number;
}

// ---------------------------------------------------------------------------
// Event registry
// ---------------------------------------------------------------------------

export interface CommanderInput {
  card_id: string | null;
  name: string;
}

/** Payload of every registered event type. Optional keys may be absent or null; both mean "not given". */
export interface EventPayloads {
  game_created: { settings: GameSettings };
  settings_changed: Partial<GameSettings>;
  seat_joined: { seat: number; player_id: string | null; name: string };
  seat_left: { seat: number };
  seat_moved: { from: number; to: number; correction?: boolean | null };
  seat_claimed: { seat: number; player_id: string };
  deck_assigned: { seat: number; deck_version_id: string; commanders: CommanderInput[] };
  commanders_set: { seat: number; commanders: CommanderInput[] };
  control_mode_changed: { mode: ControlMode };
  game_started: { first_seat: number; started_at: string };
  turn_passed: Record<string, never>;
  life_changed: { seat: number; delta: number; source_seat?: number | null };
  commander_damage: {
    source_seat: number;
    commander: number;
    target_seat: number;
    amount: number;
  };
  poison_changed: { seat: number; delta: number; source_seat?: number | null };
  counter_changed: { seat: number; counter: CounterName; delta: number };
  commander_cast: { seat: number; commander: number; delta: number };
  monarch_set: { seat: number };
  initiative_set: { seat: number };
  drain: { source_seat: number; target_seat: number; amount: number };
  group_drain: { source_seat: number; amount: number; gain: boolean };
  player_conceded: { seat: number };
  player_eliminated: { seat: number; cause?: WinCondition | null };
  player_revived: { seat: number; reason: string };
  value_set: {
    seat: number;
    field: ValueSetField;
    value: number;
    source_seat?: number | null;
    commander?: number | null;
  };
  game_end_requested: {
    winner_seat?: number | null;
    win_condition?: WinCondition | null;
    win_condition_note?: string | null;
    no_winner?: boolean | null;
  };
  game_ended: { summary: Record<string, unknown> };
  game_abandoned: { reason: AbandonReason };
  game_resumed: Record<string, never>;
  event_reverted: { target_seq: number };
}

export type EventType = keyof EventPayloads;

/** Every registered event type, in contract order. Vector coverage (S-6.1.2) iterates this list. */
export const EVENT_TYPES = [
  'game_created',
  'settings_changed',
  'seat_joined',
  'seat_left',
  'seat_moved',
  'seat_claimed',
  'deck_assigned',
  'commanders_set',
  'control_mode_changed',
  'game_started',
  'turn_passed',
  'life_changed',
  'commander_damage',
  'poison_changed',
  'counter_changed',
  'commander_cast',
  'monarch_set',
  'initiative_set',
  'drain',
  'group_drain',
  'player_conceded',
  'player_eliminated',
  'player_revived',
  'value_set',
  'game_end_requested',
  'game_ended',
  'game_abandoned',
  'game_resumed',
  'event_reverted',
] as const satisfies readonly EventType[];

export type GameEvent = { [T in EventType]: EventEnvelope<T, EventPayloads[T]> }[EventType];

// ---------------------------------------------------------------------------
// Error codes
// ---------------------------------------------------------------------------

/** Codes a reducer may return. Both reducers return exactly these strings. */
export const REDUCER_ERROR_CODES = [
  'unsupported_version',
  'unknown_event_type',
  'invalid_event',
  'game_mismatch',
  'out_of_order',
  'invalid_payload',
  'game_closed',
  'invalid_phase',
  'invalid_setting',
  'invalid_amount',
  'unknown_seat',
  'unknown_commander',
  'seat_eliminated',
  'seat_not_eliminated',
  'seat_taken',
  'table_full',
  'not_enough_players',
  'not_revertible',
  'already_reverted',
] as const;
export type ReducerErrorCode = (typeof REDUCER_ERROR_CODES)[number];

/** Codes only the server's authorization layer produces; a reducer never returns them. */
export const SERVER_REJECT_CODES = ['forbidden', 'undo_window_expired'] as const;
export type ServerRejectCode = (typeof SERVER_REJECT_CODES)[number];

export interface ReducerError {
  code: ReducerErrorCode;
}

export type ApplyResult = { ok: true; state: GameState } | { ok: false; error: ReducerError };
