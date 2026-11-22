import { describe, expect, it } from 'vitest';
import { canonicalJson } from './canonical-json';
import {
  activeGame,
  after,
  codeOf,
  joined,
  lobbyState,
  make,
  moved,
  run,
  seated,
  started,
  withEliminated,
} from './lobby.test.helpers';
import { apply } from './reducer';
import { SETTING_DEFAULTS } from './types';
import type { GameState, Phase } from './types';

/** Unit tests of the S-6.1.6 handlers, written against CONTRACT.md section 4.4. */

function deepFreeze<T>(value: T): T {
  if (typeof value === 'object' && value !== null && !Object.isFrozen(value)) {
    Object.freeze(value);
    for (const child of Object.values(value)) deepFreeze(child);
  }
  return value;
}

const inPhase = (state: GameState, phase: Phase): GameState => ({
  ...state,
  phase,
  resume_phase: phase === 'abandoned' ? 'active' : null,
});

const COMMANDER = { card_id: 'c1', name: 'Atraxa' };
const PARTNER = { card_id: 'c2', name: 'Thrasios' };

describe('game_created (4.4.2)', () => {
  const settings = { ...SETTING_DEFAULTS, starting_life: 30, trust_mode: 'strict' };

  it('replaces the settings entirely and changes nothing else', () => {
    const before = lobbyState({ poison_threshold: 3 });
    const next = after(before, make('game_created', { settings }));
    expect(next.settings).toEqual(settings);
    expect(next).toEqual({ ...before, settings, last_seq: 1 });
  });

  it('copies only the four settings and ignores unknown keys', () => {
    const next = after(lobbyState(), make('game_created', { settings: { ...settings, x: 1 } }));
    expect(Object.keys(next.settings).sort()).toEqual(Object.keys(SETTING_DEFAULTS).sort());
  });

  it('is invalid_phase when it is not the event at seq 1, in any phase', () => {
    const e = make('game_created', { settings });
    expect(codeOf(seated(1), e)).toBe('invalid_phase');
    expect(codeOf(activeGame(2), e)).toBe('invalid_phase');
    expect(codeOf({ ...lobbyState(), last_seq: 1 }, e)).toBe('invalid_phase');
  });

  it('checks the payload shape, then the phase, then the values', () => {
    const later = { ...lobbyState(), last_seq: 4 };
    expect(codeOf(later, make('game_created', {}))).toBe('invalid_payload');
    const bad = { settings: { ...settings, starting_life: 0 } };
    expect(codeOf(later, make('game_created', bad))).toBe('invalid_phase');
    expect(codeOf(lobbyState(), make('game_created', bad))).toBe('invalid_setting');
  });

  it.each([
    ['settings absent', {}],
    ['settings null', { settings: null }],
    ['settings an array', { settings: [] }],
    ['settings a string', { settings: 'x' }],
    ['a key missing', { settings: { starting_life: 40, poison_threshold: 10 } }],
  ])('is invalid_payload with %s', (_n, payload) => {
    expect(codeOf(lobbyState(), make('game_created', payload))).toBe('invalid_payload');
  });

  it.each([
    ['starting_life', 0],
    ['starting_life', 1000],
    ['starting_life', '40'],
    ['starting_life', null],
    ['starting_life', true],
    ['commander_damage_threshold', 0],
    ['commander_damage_threshold', 100],
    ['poison_threshold', 0],
    ['poison_threshold', 100],
    ['trust_mode', 'banana'],
    ['trust_mode', 'STRICT'],
    ['trust_mode', 3],
  ])('is invalid_setting for %s = %j', (key, value) => {
    const payload = { settings: { ...SETTING_DEFAULTS, [key]: value } };
    expect(codeOf(lobbyState(), make('game_created', payload))).toBe('invalid_setting');
  });

  it.each([
    ['starting_life', 1],
    ['starting_life', 999],
    ['commander_damage_threshold', 1],
    ['commander_damage_threshold', 99],
    ['poison_threshold', 1],
    ['poison_threshold', 99],
    ['trust_mode', 'strict'],
  ])('accepts %s = %j', (key, value) => {
    const payload = { settings: { ...SETTING_DEFAULTS, [key]: value } };
    expect(codeOf(lobbyState(), make('game_created', payload))).toBe('ok');
  });
});

describe('settings_changed (4.4.3)', () => {
  it('applies a partial change and keeps the other settings', () => {
    const next = after(lobbyState(), make('settings_changed', { trust_mode: 'strict' }));
    expect(next.settings).toEqual({ ...SETTING_DEFAULTS, trust_mode: 'strict' });
  });

  it('accepts an empty payload and changes nothing but last_seq', () => {
    const before = seated(2);
    expect(after(before, make('settings_changed', {}))).toEqual({ ...before, last_seq: 3 });
  });

  it('rewrites the life of every seat when starting_life changes, and only then', () => {
    const next = after(seated(3), make('settings_changed', { starting_life: 25 }));
    expect(next.seats.map((s) => s.life)).toEqual([25, 25, 25]);
    const same = after(next, make('settings_changed', { poison_threshold: 5 }));
    expect(same.seats.map((s) => s.life)).toEqual([25, 25, 25]);
    expect(after(next, joined(4)).seats[3]!.life).toBe(25);
  });

  it('rejects the whole event when one key is bad, and applies none of it', () => {
    const before = seated(2);
    const bad = make('settings_changed', { starting_life: 30, poison_threshold: 0 });
    expect(apply(before, bad)).toEqual({ ok: false, error: { code: 'invalid_setting' } });
  });

  it.each([
    ['null', null],
    ['a string', '40'],
    ['0', 0],
    ['1000', 1000],
    ['a boolean', false],
  ])('treats starting_life %s as invalid_setting', (_n, value) => {
    expect(codeOf(lobbyState(), make('settings_changed', { starting_life: value }))).toBe(
      'invalid_setting',
    );
  });

  it('is invalid_phase outside the lobby, before the values are checked', () => {
    for (const phase of ['active', 'ending', 'finished', 'abandoned'] as const) {
      const state = inPhase(activeGame(2), phase);
      expect(codeOf(state, make('settings_changed', { trust_mode: 'strict' }))).toBe(
        'invalid_phase',
      );
      expect(codeOf(state, make('settings_changed', { starting_life: 0 }))).toBe('invalid_phase');
      expect(codeOf(state, make('settings_changed', {}))).toBe('invalid_phase');
    }
  });

  it('ignores unknown keys', () => {
    const next = after(lobbyState(), make('settings_changed', { trust_mode: 'strict', x: 1 }));
    expect(next.settings.trust_mode).toBe('strict');
  });
});

describe('seat_joined (4.4.4)', () => {
  it('creates a seat with the documented fields and appends it to the order', () => {
    const next = after(lobbyState({ starting_life: 33 }), joined(4, 'Ana', 'p-ana'));
    expect(next.seats).toEqual([
      {
        seat: 4,
        player_id: 'p-ana',
        name: 'Ana',
        deck_version_id: null,
        commanders: [{ card_id: null, name: '' }],
        life: 33,
        poison: 0,
        energy: 0,
        experience: 0,
        commander_cast: [0],
        elimination_reason: null,
        eliminated_by: null,
        eliminated_at_seq: null,
        eliminated_round: null,
        elimination_cause: null,
      },
    ]);
    expect(next.order).toEqual([4]);
  });

  it('keeps seats sorted by id and order by join sequence', () => {
    const next = run(lobbyState(), joined(3), joined(1), joined(2));
    expect(next.seats.map((s) => s.seat)).toEqual([1, 2, 3]);
    expect(next.order).toEqual([3, 1, 2]);
  });

  it('takes up to 8 seats and rejects the 9th with table_full, before seat_taken', () => {
    const full = seated(8);
    expect(full.seats).toHaveLength(8);
    expect(codeOf(full, joined(1))).toBe('table_full');
    expect(codeOf(full, joined(8))).toBe('table_full');
  });

  it('rejects a seat id already in use with seat_taken', () => {
    expect(codeOf(seated(3), joined(2))).toBe('seat_taken');
  });

  it('does not check that a player id is unique', () => {
    const state = run(lobbyState(), joined(1, 'A', 'p'), joined(2, 'B', 'p'));
    expect(state.seats.map((s) => s.player_id)).toEqual(['p', 'p']);
  });

  it.each([
    ['seat 0', { seat: 0, player_id: null, name: 'x' }],
    ['seat 9', { seat: 9, player_id: null, name: 'x' }],
    ['seat a string', { seat: '1', player_id: null, name: 'x' }],
    ['seat missing', { player_id: null, name: 'x' }],
    ['name missing', { seat: 1, player_id: null }],
    ['name a number', { seat: 1, player_id: null, name: 1 }],
    ['name null', { seat: 1, player_id: null, name: null }],
    ['player_id missing', { seat: 1, name: 'x' }],
    ['player_id empty', { seat: 1, player_id: '', name: 'x' }],
    ['player_id a number', { seat: 1, player_id: 3, name: 'x' }],
  ])('is invalid_payload with %s', (_n, payload) => {
    expect(codeOf(lobbyState(), make('seat_joined', payload))).toBe('invalid_payload');
  });

  it('is invalid_phase after the start, before table_full and after the payload shape', () => {
    const game = activeGame(8);
    expect(codeOf(game, joined(1))).toBe('invalid_phase');
    expect(codeOf(game, make('seat_joined', { seat: 9, player_id: null, name: '' }))).toBe(
      'invalid_payload',
    );
  });

  it.each(['ending', 'finished', 'abandoned'] as const)('is invalid_phase in %s', (phase) => {
    expect(codeOf(inPhase(seated(2), phase), joined(3))).toBe('invalid_phase');
  });

  it('accepts an empty name and a guest seat', () => {
    const next = after(lobbyState(), make('seat_joined', { seat: 1, player_id: null, name: '' }));
    expect(next.seats[0]).toMatchObject({ player_id: null, name: '' });
  });
});

describe('seat_left (4.4.5)', () => {
  it('removes the seat and its id from the order, keeping the others', () => {
    const swapped = run(seated(4), moved(1, 3));
    const next = after(swapped, make('seat_left', { seat: 2 }));
    expect(next.seats.map((s) => s.seat)).toEqual([1, 3, 4]);
    expect(next.order).toEqual([3, 1, 4]);
  });

  it('lets a seat join again as a new seat at the end of the order', () => {
    const left = after(seated(3), make('seat_left', { seat: 2 }));
    const again = after(left, make('seat_joined', { seat: 2, player_id: 'p', name: 'New' }));
    expect(again.order).toEqual([1, 3, 2]);
    expect(again.seats[1]).toMatchObject({ seat: 2, name: 'New', deck_version_id: null });
  });

  it('is unknown_seat for a seat that is not at the table', () => {
    expect(codeOf(seated(2), make('seat_left', { seat: 3 }))).toBe('unknown_seat');
    expect(codeOf(lobbyState(), make('seat_left', { seat: 1 }))).toBe('unknown_seat');
  });

  it.each([[0], [9], ['1'], [null], [1.5]])('is invalid_payload for seat %j', (seat) => {
    expect(codeOf(seated(2), make('seat_left', { seat }))).toBe('invalid_payload');
  });

  it.each(['active', 'ending', 'finished', 'abandoned'] as const)(
    'is invalid_phase in %s, before the seat is looked up',
    (phase) => {
      const state = inPhase(activeGame(2), phase);
      expect(codeOf(state, make('seat_left', { seat: 1 }))).toBe('invalid_phase');
      expect(codeOf(state, make('seat_left', { seat: 7 }))).toBe('invalid_phase');
    },
  );
});

describe('seat_moved (4.4.6)', () => {
  it('swaps two seats in the order in the lobby and leaves the seats alone', () => {
    const before = seated(4);
    const next = after(before, moved(4, 2));
    expect(next.order).toEqual([1, 4, 3, 2]);
    expect(next.seats).toEqual(before.seats);
  });

  it.each([
    [1, 2, [2, 1, 3, 4]],
    [2, 1, [2, 1, 3, 4]],
    [1, 4, [4, 2, 3, 1]],
    [2, 4, [1, 4, 3, 2]],
    [3, 3, [1, 2, 3, 4]],
  ])('swaps %i and %i to %j', (from, to, order) => {
    expect(after(seated(4), moved(from, to)).order).toEqual(order);
  });

  it('swaps by position in the current order, not by seat id', () => {
    const next = run(seated(3), moved(1, 3), moved(3, 2));
    expect(next.order).toEqual([2, 3, 1]);
  });

  it.each([true, false, null, undefined])('ignores correction = %s in the lobby', (c) => {
    expect(after(seated(3), moved(1, 2, c)).order).toEqual([2, 1, 3]);
  });

  it('is unknown_seat for a seat that is not at the table', () => {
    expect(codeOf(seated(3), moved(4, 1))).toBe('unknown_seat');
    expect(codeOf(seated(3), moved(1, 4))).toBe('unknown_seat');
  });

  it.each([
    [{ from: 0, to: 1 }],
    [{ from: 1, to: 9 }],
    [{ from: '1', to: 2 }],
    [{ from: 1 }],
    [{ to: 2 }],
    [{ from: 1, to: 2, correction: 'true' }],
    [{ from: 1, to: 2, correction: 1 }],
  ])('is invalid_payload for %j', (payload) => {
    expect(codeOf(seated(3), make('seat_moved', payload))).toBe('invalid_payload');
  });

  describe('in an active game', () => {
    it('is invalid_phase without correction: true', () => {
      const game = activeGame(4, 3);
      expect(codeOf(game, moved(4, 2))).toBe('invalid_phase');
      expect(codeOf(game, moved(4, 2, false))).toBe('invalid_phase');
      expect(codeOf(game, moved(4, 2, null))).toBe('invalid_phase');
      expect(codeOf(game, moved(9, 2))).toBe('invalid_payload');
      expect(codeOf(game, moved(5, 2))).toBe('invalid_phase');
    });

    it('swaps the order and changes nothing else: the same seat stays active', () => {
      const game = activeGame(4, 3);
      const next = after(game, moved(4, 2, true));
      expect(next).toEqual({ ...game, order: [1, 4, 3, 2], last_seq: game.last_seq + 1 });
      expect(next.active_seat).toBe(3);
      expect([next.turn, next.round]).toEqual([1, 1]);
    });

    it('keeps the active seat when it is one of the two', () => {
      const next = after(activeGame(4, 3), moved(3, 1, true));
      expect(next.order).toEqual([3, 2, 1, 4]);
      expect(next.active_seat).toBe(3);
    });

    it('is unknown_seat for a seat that is not at the table', () => {
      expect(codeOf(activeGame(4), moved(5, 1, true))).toBe('unknown_seat');
    });

    it('lets the following turn_passed events follow the new order', () => {
      const corrected = after(activeGame(4, 3), moved(4, 2, true));
      const one = after(corrected, make('turn_passed'));
      expect([one.active_seat, one.turn, one.round]).toEqual([2, 2, 1]);
      const two = after(one, make('turn_passed'));
      expect([two.active_seat, two.turn, two.round]).toEqual([1, 3, 1]);
    });
  });

  describe('in an ending game (review screen)', () => {
    const ending = () => inPhase(activeGame(4, 3), 'ending');

    it('is invalid_phase without correction: true', () => {
      expect(codeOf(ending(), moved(4, 2))).toBe('invalid_phase');
      expect(codeOf(ending(), moved(4, 2, false))).toBe('invalid_phase');
      expect(codeOf(ending(), moved(4, 2, null))).toBe('invalid_phase');
    });

    it('swaps the order with correction: true and changes nothing else', () => {
      const game = ending();
      const next = after(game, moved(4, 2, true));
      expect(next).toEqual({ ...game, order: [1, 4, 3, 2], last_seq: game.last_seq + 1 });
      expect(next.active_seat).toBe(3);
    });

    it('is unknown_seat for a seat that is not at the table', () => {
      expect(codeOf(ending(), moved(5, 1, true))).toBe('unknown_seat');
    });
  });

  it.each(['finished', 'abandoned'] as const)(
    'is invalid_phase in %s, even with correction: true',
    (phase) => {
      expect(codeOf(inPhase(activeGame(4), phase), moved(1, 2, true))).toBe('invalid_phase');
    },
  );
});

describe('deck_assigned and commanders_set (4.4.7, 4.4.8)', () => {
  const deck = (over: Record<string, unknown> = {}) =>
    make('deck_assigned', { seat: 2, deck_version_id: 'dv1', commanders: [COMMANDER], ...over });
  const set = (over: Record<string, unknown> = {}) =>
    make('commanders_set', { seat: 2, commanders: [COMMANDER, PARTNER], ...over });

  it('deck_assigned sets the deck, replaces the commanders and zeroes the cast counters', () => {
    const next = after(seated(3), deck({ commanders: [COMMANDER, PARTNER] }));
    expect(next.seats[1]).toMatchObject({
      deck_version_id: 'dv1',
      commanders: [COMMANDER, PARTNER],
      commander_cast: [0, 0],
    });
    expect(next.seats[0]!.deck_version_id).toBeNull();
    expect(next.seats[2]!.commanders).toEqual([{ card_id: null, name: '' }]);
  });

  it('deck_assigned replaces an earlier deck and commanders', () => {
    const next = run(
      seated(3),
      deck({ commanders: [COMMANDER, PARTNER] }),
      deck({
        deck_version_id: 'dv2',
      }),
    );
    expect(next.seats[1]).toMatchObject({
      deck_version_id: 'dv2',
      commanders: [COMMANDER],
      commander_cast: [0],
    });
  });

  it('commanders_set replaces the commanders and clears the deck', () => {
    const next = run(seated(3), deck(), set());
    expect(next.seats[1]).toMatchObject({
      deck_version_id: null,
      commanders: [COMMANDER, PARTNER],
      commander_cast: [0, 0],
    });
  });

  it('stores only card_id and name of a commander and copies them', () => {
    const payload = { seat: 1, commanders: [{ card_id: null, name: 'N', extra: 1 }] };
    const e = make('commanders_set', payload);
    const next = after(seated(2), e);
    expect(next.seats[0]!.commanders).toEqual([{ card_id: null, name: 'N' }]);
    expect(next.seats[0]!.commanders[0]).not.toBe(payload.commanders[0]);
  });

  it.each([
    ['empty', []],
    ['three', [COMMANDER, PARTNER, COMMANDER]],
    ['not an array', COMMANDER],
    ['null', null],
    ['an item that is not an object', ['x']],
    ['a name that is missing', [{ card_id: 'c' }]],
    ['a card_id that is missing', [{ name: 'n' }]],
    ['a card_id that is empty', [{ card_id: '', name: 'n' }]],
    ['a bad second item', [COMMANDER, { name: 5, card_id: null }]],
  ])('is invalid_payload for commanders: %s', (_n, commanders) => {
    expect(codeOf(seated(3), deck({ commanders }))).toBe('invalid_payload');
    expect(codeOf(seated(3), set({ commanders }))).toBe('invalid_payload');
  });

  it.each([
    ['deck_version_id empty', { deck_version_id: '' }],
    ['deck_version_id null', { deck_version_id: null }],
    ['deck_version_id missing', { deck_version_id: undefined }],
    ['seat 0', { seat: 0 }],
    ['seat missing', { seat: undefined }],
  ])('deck_assigned is invalid_payload for %s', (_n, over) => {
    const e = deck(over);
    if ('seat' in over && over.seat === undefined)
      delete (e.payload as Record<string, unknown>).seat;
    if ('deck_version_id' in over && over.deck_version_id === undefined) {
      delete (e.payload as Record<string, unknown>).deck_version_id;
    }
    expect(codeOf(seated(3), e)).toBe('invalid_payload');
  });

  it('is unknown_seat for a seat that is not at the table', () => {
    expect(codeOf(seated(2), deck({ seat: 3 }))).toBe('unknown_seat');
    expect(codeOf(seated(2), set({ seat: 3 }))).toBe('unknown_seat');
  });

  it.each(['active', 'ending', 'finished', 'abandoned'] as const)(
    'is invalid_phase in %s, after the payload shape and before the seat',
    (phase) => {
      const state = inPhase(activeGame(2), phase);
      expect(codeOf(state, deck())).toBe('invalid_phase');
      expect(codeOf(state, set())).toBe('invalid_phase');
      expect(codeOf(state, deck({ seat: 5 }))).toBe('invalid_phase');
      expect(codeOf(state, deck({ commanders: [] }))).toBe('invalid_payload');
    },
  );
});

describe('game_started (4.4.9)', () => {
  it('starts the game: phase, active seat, turn 1, round 1, started_at and the starting life', () => {
    const before = seated(4);
    const next = after(before, started(3));
    expect(next).toEqual({
      ...before,
      phase: 'active',
      active_seat: 3,
      first_seat: 3,
      turn: 1,
      round: 1,
      started_at: '2026-11-21T12:00:00.000Z',
      last_seq: before.last_seq + 1,
    });
    expect(next.seats.every((s) => s.life === 40)).toBe(true);
  });

  it('puts every seat at the starting life of the settings', () => {
    const state = run(seated(3), make('settings_changed', { starting_life: 17 }));
    expect(after(state, started(1)).seats.map((s) => s.life)).toEqual([17, 17, 17]);
  });

  it('sets life even when a seat in the lobby carries another value (hand-built state)', () => {
    const odd = { ...seated(2), seats: seated(2).seats.map((s) => ({ ...s, life: 5 })) };
    expect(after(odd, started(1)).seats.map((s) => s.life)).toEqual([40, 40]);
  });

  it('stores started_at as sent, whatever the string', () => {
    const e = make('game_started', { first_seat: 1, started_at: 'not a timestamp' });
    expect(after(seated(2), e).started_at).toBe('not a timestamp');
  });

  it('starts with 2 and with 8 seats', () => {
    expect(after(seated(2), started(2)).phase).toBe('active');
    expect(after(seated(8), started(8)).active_seat).toBe(8);
  });

  it('keeps the seat order set in the lobby', () => {
    const next = after(run(seated(4), moved(1, 4)), started(2));
    expect(next.order).toEqual([4, 2, 3, 1]);
  });

  it('is not_enough_players with one seat', () => {
    expect(codeOf(seated(1), started(1))).toBe('not_enough_players');
  });

  it('is unknown_seat before not_enough_players when first_seat is not at the table', () => {
    expect(codeOf(lobbyState(), started(1))).toBe('unknown_seat');
    expect(codeOf(seated(1), started(2))).toBe('unknown_seat');
    expect(codeOf(seated(4), started(5))).toBe('unknown_seat');
  });

  it.each([
    [{ first_seat: 0, started_at: 'x' }],
    [{ first_seat: 9, started_at: 'x' }],
    [{ first_seat: '1', started_at: 'x' }],
    [{ first_seat: null, started_at: 'x' }],
    [{ started_at: 'x' }],
    [{ first_seat: 1 }],
    [{ first_seat: 1, started_at: null }],
    [{ first_seat: 1, started_at: 5 }],
  ])('is invalid_payload for %j', (payload) => {
    expect(codeOf(seated(2), make('game_started', payload))).toBe('invalid_payload');
  });

  it.each(['active', 'ending', 'finished', 'abandoned'] as const)(
    'is invalid_phase in %s, before the seat is looked up',
    (phase) => {
      const state = inPhase(activeGame(2), phase);
      expect(codeOf(state, started(1))).toBe('invalid_phase');
      expect(codeOf(state, started(7))).toBe('invalid_phase');
    },
  );
});

describe('turn_passed (4.4.10)', () => {
  const pass = (state: GameState): GameState => after(state, make('turn_passed'));
  const at = (s: GameState) => [s.active_seat, s.turn, s.round];

  it('moves to the next seat, adds a turn, and starts a round when play reaches the head', () => {
    let s = activeGame(4);
    const seen = [at(s)];
    for (let i = 0; i < 9; i++) seen.push(at((s = pass(s))));
    expect(seen).toEqual([
      [1, 1, 1],
      [2, 2, 1],
      [3, 3, 1],
      [4, 4, 1],
      [1, 5, 2],
      [2, 6, 2],
      [3, 7, 2],
      [4, 8, 2],
      [1, 9, 3],
      [2, 10, 3],
    ]);
  });

  it('changes only active_seat, turn, round and last_seq', () => {
    const game = activeGame(3);
    const next = pass(game);
    expect(next).toEqual({
      ...game,
      active_seat: 2,
      turn: 2,
      round: 1,
      last_seq: game.last_seq + 1,
    });
  });

  it('with a first seat in the middle, rounds are full rounds that start at the first seat', () => {
    let s = activeGame(4, 3);
    const seen = [at(s)];
    for (let i = 0; i < 12; i++) seen.push(at((s = pass(s))));
    expect(seen.map(([a, , r]) => `${a}/${r}`)).toEqual([
      ...[3, 4, 1, 2].map((a) => `${a}/1`),
      ...[3, 4, 1, 2].map((a) => `${a}/2`),
      ...[3, 4, 1, 2].map((a) => `${a}/3`),
      '3/4',
    ]);
    expect(s.first_seat).toBe(3);
  });

  it('with two seats, the round goes up every second pass', () => {
    let s = activeGame(2);
    const seen = [];
    for (let i = 0; i < 4; i++) seen.push(at((s = pass(s))));
    expect(seen).toEqual([
      [2, 2, 1],
      [1, 3, 2],
      [2, 4, 2],
      [1, 5, 3],
    ]);
  });

  it('follows seat positions, not seat ids', () => {
    const state = run(
      lobbyState(),
      joined(5),
      joined(2),
      joined(7),
      make('game_started', { first_seat: 5, started_at: 't' }),
    );
    expect(at(pass(state))).toEqual([2, 2, 1]);
    expect(at(pass(pass(state)))).toEqual([7, 3, 1]);
    expect(at(pass(pass(pass(state))))).toEqual([5, 4, 2]);
  });

  it('is invalid_phase outside an active game', () => {
    const e = make('turn_passed');
    expect(codeOf(lobbyState(), e)).toBe('invalid_phase');
    expect(codeOf(seated(3), e)).toBe('invalid_phase');
    for (const phase of ['finished', 'abandoned'] as const) {
      expect(codeOf(inPhase(activeGame(3), phase), e)).toBe('invalid_phase');
    }
  });

  it('is allowed in ending, with the same effect as in active', () => {
    const game = inPhase(activeGame(3), 'ending');
    const next = pass(game);
    expect(next).toEqual({
      ...game,
      active_seat: 2,
      turn: 2,
      round: 1,
      last_seq: game.last_seq + 1,
    });
    const wrapped = pass(pass(next));
    expect([wrapped.active_seat, wrapped.turn, wrapped.round]).toEqual([1, 4, 2]);
  });

  it('does not rely on an active_seat check: a hand-built null active seat does not throw', () => {
    const broken = { ...activeGame(3), active_seat: null };
    expect(() => apply(broken, make('turn_passed'))).not.toThrow();
  });

  it('ignores unknown payload keys', () => {
    expect(codeOf(activeGame(2), make('turn_passed', { note: 'x' }))).toBe('ok');
  });

  describe('with eliminated seats (hand-built states: no event can eliminate yet)', () => {
    /** A state of the shape the contract's example table starts from (4.4.10). */
    const game = (
      order: number[],
      first: number,
      dead: number[],
      active: number,
      turn = 1,
      round = 1,
    ): GameState => ({
      ...withEliminated(activeGame(4), ...dead),
      order,
      first_seat: first,
      active_seat: active,
      turn,
      round,
    });
    const natural = [1, 2, 3, 4];

    // Rows 1 to 19 of the worked-example table in CONTRACT.md 4.4.10:
    // [row, order, F, eliminated, a, turn, round, n, turn after, round after].
    it.each([
      [1, natural, 1, [], 1, 1, 1, 2, 2, 1],
      [2, natural, 1, [], 4, 4, 1, 1, 5, 2],
      [3, natural, 3, [], 3, 1, 1, 4, 2, 1],
      [4, natural, 3, [], 4, 2, 1, 1, 3, 1],
      [5, natural, 3, [], 2, 4, 1, 3, 5, 2],
      [6, natural, 3, [], 2, 12, 3, 3, 13, 4],
      [7, natural, 1, [4], 3, 3, 1, 1, 4, 2],
      [8, natural, 3, [4], 3, 1, 1, 1, 2, 1],
      [9, natural, 1, [1], 2, 2, 1, 3, 3, 1],
      [10, natural, 1, [1], 4, 4, 1, 2, 5, 2],
      [11, natural, 1, [1], 1, 1, 1, 2, 2, 1],
      [12, natural, 3, [3], 2, 4, 1, 4, 5, 2],
      [13, natural, 4, [4], 3, 4, 1, 1, 5, 2],
      [14, [3, 2, 1, 4], 1, [], 4, 2, 1, 3, 3, 1],
      [15, natural, 1, [2, 3, 4], 1, 1, 1, 1, 2, 2],
      [16, natural, 1, [1, 2, 3], 4, 4, 3, 4, 5, 4],
      [17, [3, 2, 1, 4], 1, [], 2, 2, 1, 1, 3, 2],
      [18, [1, 3, 2, 4], 1, [], 3, 3, 1, 2, 4, 1],
      [19, [4, 2, 3, 1], 1, [], 3, 3, 1, 1, 4, 2],
    ])(
      'example row %i: order %j, F %i, eliminated %j, active %i',
      (_row, order, f, dead, a, turn, round, n, turnAfter, roundAfter) => {
        const next = pass(game(order, f, dead, a, turn, round));
        expect([next.active_seat, next.turn, next.round]).toEqual([n, turnAfter, roundAfter]);
        expect(next.first_seat).toBe(f);
      },
    );

    it('skips an eliminated seat (criterion 6): seat 3 active, seat 4 out, seat 1 next, round + 1', () => {
      const next = pass(game(natural, 1, [4], 3, 3));
      expect([next.active_seat, next.turn, next.round]).toEqual([1, 4, 2]);
    });

    it('skips several eliminated seats in a row', () => {
      const next = pass(game(natural, 1, [2, 3], 1));
      expect(next.active_seat).toBe(4);
      expect(next.round).toBe(1);
    });

    it('passes to itself when it is the only seat left, and every pass is a new round', () => {
      const next = pass(game(natural, 1, [1, 2, 3], 4));
      expect([next.active_seat, next.round]).toEqual([4, 2]);
      expect(pass(next).round).toBe(3);
    });

    it('row 11 then row 10: F out on its own turn, the round goes up when play passes its place', () => {
      const one = pass(game(natural, 1, [1], 1));
      expect([one.active_seat, one.round]).toEqual([2, 1]);
      const two = pass(pass(pass(one)));
      expect([two.active_seat, two.turn, two.round]).toEqual([2, 5, 2]);
    });

    it('is allowed in ending with eliminated seats', () => {
      const ending = inPhase(game(natural, 1, [4], 3, 3), 'ending');
      const next = pass(ending);
      expect([next.active_seat, next.turn, next.round]).toEqual([1, 4, 2]);
    });

    it('is invalid_phase when every seat is eliminated', () => {
      expect(codeOf(game(natural, 1, [1, 2, 3, 4], 1), make('turn_passed'))).toBe('invalid_phase');
    });

    it('moves on from an active seat that was itself eliminated', () => {
      const next = pass(game(natural, 1, [2], 2));
      expect(next.active_seat).toBe(3);
    });
  });
});

describe('first_seat (3.1)', () => {
  it('is null before the start, set by game_started, and never changed afterwards', () => {
    expect(seated(3).first_seat).toBeNull();
    let s = activeGame(4, 3);
    expect(s.first_seat).toBe(3);
    s = run(s, moved(3, 1, true), make('turn_passed'), make('turn_passed'), moved(2, 4, true));
    expect(s.first_seat).toBe(3);
  });
});

describe('purity of the S-6.1.6 handlers', () => {
  const cases: [string, GameState, Record<string, unknown>][] = [
    ['game_created', lobbyState(), { settings: SETTING_DEFAULTS }],
    ['settings_changed', seated(2), { starting_life: 12 }],
    ['seat_joined', seated(2), { seat: 5, player_id: 'p', name: 'n' }],
    ['seat_left', seated(3), { seat: 2 }],
    ['seat_moved', seated(3), { from: 1, to: 3 }],
    ['deck_assigned', seated(2), { seat: 1, deck_version_id: 'd', commanders: [COMMANDER] }],
    ['commanders_set', seated(2), { seat: 2, commanders: [COMMANDER, PARTNER] }],
    ['game_started', seated(3), { first_seat: 2, started_at: 't' }],
    ['turn_passed', activeGame(3), {}],
  ];

  it.each(cases)(
    '%s never mutates its inputs and returns fresh objects',
    (type, state, payload) => {
      const s = deepFreeze(structuredClone(state));
      const e = deepFreeze(make(type, structuredClone(payload)));
      const before = canonicalJson(s);
      const r = apply(s, e);
      expect(r.ok).toBe(true);
      expect(canonicalJson(s)).toBe(before);
      expect(canonicalJson(e.payload)).toBe(canonicalJson(payload));
    },
  );

  it('game_created does not share its settings object with the payload', () => {
    const settings = { ...SETTING_DEFAULTS };
    const next = after(lobbyState(), make('game_created', { settings }));
    expect(next.settings).not.toBe(settings);
  });

  it('a rejected event changes nothing: the caller keeps the old state', () => {
    const state = seated(2);
    const before = canonicalJson(state);
    expect(codeOf(state, joined(1))).toBe('seat_taken');
    expect(canonicalJson(state)).toBe(before);
  });
});
