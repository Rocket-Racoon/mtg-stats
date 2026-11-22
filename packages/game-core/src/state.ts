import { SCHEMA_VERSION, SETTING_DEFAULTS, type GameSettings, type GameState } from './types';
import { hasLoneSurrogate } from './unicode';

/**
 * The state before any event: an empty lobby (CONTRACT.md 3.5).
 *
 * `gameId` must be a non-empty string with no lone surrogate, and `settings` (optional) must be
 * an object whose keys are all setting names; missing keys take `SETTING_DEFAULTS`. Otherwise this
 * throws. It is not a reducer, so it returns no reducer error code. Values are copied as given:
 * neither types nor ranges are validated here (`game_created` and `settings_changed` do that).
 */
export function initialState(gameId: string, settings: Partial<GameSettings> = {}): GameState {
  if (typeof gameId !== 'string' || gameId === '' || hasLoneSurrogate(gameId)) {
    throw new TypeError('initialState: game_id must be a non-empty well-formed string');
  }
  if (typeof settings !== 'object' || settings === null || Array.isArray(settings)) {
    throw new TypeError('initialState: settings must be an object');
  }
  for (const key of Object.keys(settings)) {
    if (!Object.hasOwn(SETTING_DEFAULTS, key)) {
      throw new TypeError('initialState: unknown setting ' + key);
    }
  }
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
    first_seat: null,
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
    reverted: [],
    last_seq: 0,
  };
}
