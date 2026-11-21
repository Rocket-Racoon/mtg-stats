import { SCHEMA_VERSION, SETTING_DEFAULTS, type GameSettings, type GameState } from './types';

/**
 * The state before any event: an empty lobby. Settings default to `SETTING_DEFAULTS`.
 * Settings are not validated here; `game_created` and `settings_changed` validate them.
 */
export function initialState(gameId: string, settings: Partial<GameSettings> = {}): GameState {
  return {
    v: SCHEMA_VERSION,
    game_id: gameId,
    phase: 'lobby',
    resume_phase: null,
    settings: { ...SETTING_DEFAULTS, ...settings },
    control_mode: 'own_seat',
    seats: [],
    order: [],
    active_seat: null,
    turn: 0,
    round: 0,
    monarch: null,
    initiative: null,
    commander_damage: {},
    no_winner: false,
    winner_seat: null,
    win_condition: null,
    win_condition_note: '',
    started_at: null,
    voided: [],
    last_seq: 0,
  };
}
