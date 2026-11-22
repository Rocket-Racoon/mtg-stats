import { readdirSync, readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import {
  activeGame,
  codeOf,
  joined,
  lobbyState,
  make,
  moved,
  NO_OP_TYPES,
  run,
  S_6_1_6_TYPES,
  seated,
  started,
  withEliminated,
} from './lobby.test.helpers';
import { apply } from './reducer';
import { EVENT_TYPES, REDUCER_ERROR_CODES, SETTING_DEFAULTS } from './types';
import type { EventEnvelope, GameState, Phase } from './types';

/**
 * Acceptance tests of S-6.1.6 (lobby, seat and turn events), written from the six acceptance
 * criteria of the story and from CONTRACT.md 4.4, not from the reducer. The shared vectors are
 * the primary proof for everything reachable through events. This file adds what a vector cannot
 * say: states with eliminated seats (no event can eliminate a seat before S-6.1.3), phases a log
 * cannot reach yet (`ending`, `finished`, `abandoned`), and the link between CONTRACT.md 4.4 and
 * the code.
 *
 * NOTE FOR S-6.1.3: criterion 6's eliminated-seat case has NO shared vector. S-6.1.3 provides the
 * first events that eliminate a seat and MUST add vectors for the cases in the "criterion 6"
 * block below; until then they rest on these unit tests (and their Python twin).
 */

const contract = readFileSync(new URL('../CONTRACT.md', import.meta.url), 'utf8');

const inPhase = (state: GameState, phase: Phase): GameState => ({
  ...state,
  phase,
  resume_phase: phase === 'abandoned' ? 'active' : null,
});
const ev = (type: string, payload: Record<string, unknown> = {}): EventEnvelope =>
  make(type, payload);
const step = (state: GameState, e: EventEnvelope): GameState => run(state, e);

// ---------------------------------------------------------------------------------------------
describe('criterion 1: seat_moved in a lobby swaps two seats in the seat order', () => {
  const lobby4 = () => seated(4);

  it('swaps seat 4 and seat 2 of a 4-seat lobby: [1,2,3,4] becomes [1,4,3,2]', () => {
    expect(step(lobby4(), moved(4, 2)).order).toEqual([1, 4, 3, 2]);
  });

  it('swaps adjacent seats: 1 and 2 give [2,1,3,4]', () => {
    expect(step(lobby4(), moved(1, 2)).order).toEqual([2, 1, 3, 4]);
  });

  it('swaps the first and the last seat: [4,2,3,1]', () => {
    expect(step(lobby4(), moved(1, 4)).order).toEqual([4, 2, 3, 1]);
  });

  it('is symmetric: from and to can be given in either direction', () => {
    expect(step(lobby4(), moved(2, 4)).order).toEqual(step(lobby4(), moved(4, 2)).order);
  });

  it('leaves the seats array and every seat object untouched', () => {
    const before = lobby4();
    const after = step(before, moved(4, 2));
    expect(after.seats).toEqual(before.seats);
    expect(after.seats.map((s) => s.seat)).toEqual([1, 2, 3, 4]);
  });

  it('a swap of a seat with itself changes nothing but last_seq', () => {
    const before = lobby4();
    expect(step(before, moved(3, 3))).toEqual({ ...before, last_seq: before.last_seq + 1 });
  });

  it('applies in the order the events come: 4<->2 then 1<->2 gives [2,4,3,1]', () => {
    expect(step(step(lobby4(), moved(4, 2)), moved(1, 2)).order).toEqual([2, 4, 3, 1]);
  });

  it('accepts a swap with correction true, false or null in the lobby', () => {
    for (const c of [true, false, null]) {
      expect(codeOf(lobby4(), moved(4, 2, c))).toBe('ok');
    }
  });

  it('is unknown_seat when either seat is not at the table', () => {
    expect(codeOf(lobby4(), moved(4, 5))).toBe('unknown_seat');
    expect(codeOf(lobby4(), moved(5, 2))).toBe('unknown_seat');
  });
});

// ---------------------------------------------------------------------------------------------
describe('criterion 2: seat_moved as a mid-game correction', () => {
  // Seat 3 is the active player: first seat 3.
  const game = () => activeGame(4, 3);

  it('swaps the seats, keeps seat 3 (the same player) active and leaves turn and round alone', () => {
    const before = game();
    const after = step(before, moved(4, 2, true));
    expect(after.order).toEqual([1, 4, 3, 2]);
    expect(after.active_seat).toBe(3);
    expect(after.seats.find((s) => s.seat === 3)?.player_id).toBe(
      before.seats.find((s) => s.seat === 3)?.player_id,
    );
    expect([after.turn, after.round]).toEqual([1, 1]);
    expect(after.first_seat).toBe(3);
  });

  it('changes nothing except order and last_seq', () => {
    const before = game();
    expect(step(before, moved(4, 2, true))).toEqual({
      ...before,
      order: [1, 4, 3, 2],
      last_seq: before.last_seq + 1,
    });
  });

  it('keeps the active seat when the active seat is one of the two that swap', () => {
    const after = step(game(), moved(3, 1, true));
    expect(after.order).toEqual([3, 2, 1, 4]);
    expect(after.active_seat).toBe(3);
    expect([after.turn, after.round]).toEqual([1, 1]);
  });

  it('later turn_passed events follow the new order, one turn each', () => {
    let s = step(game(), moved(4, 2, true));
    const seen: number[] = [];
    for (let i = 0; i < 4; i++) {
      s = step(s, ev('turn_passed'));
      seen.push(s.active_seat as number);
    }
    // [1,4,3,2] after seat 3 is 2, then 1, then 4, then back to 3 (which is the first seat).
    expect(seen).toEqual([2, 1, 4, 3]);
    expect([s.turn, s.round]).toEqual([5, 2]);
  });

  it.each([
    ['without a correction key', undefined],
    ['with correction false', false],
    ['with correction null', null],
  ])('is rejected with invalid_phase in an active game %s', (_label, correction) => {
    expect(codeOf(game(), moved(4, 2, correction))).toBe('invalid_phase');
  });

  it.each(['ending', 'finished', 'abandoned'] as const)(
    'is invalid_phase in a %s game even with correction true',
    (phase) => {
      expect(codeOf(inPhase(game(), phase), moved(4, 2, true))).toBe('invalid_phase');
    },
  );

  it.each([['1'], [1], [0], ['true'], [[]]])(
    'a correction that is not a boolean (%j) is invalid_payload',
    (c) => {
      expect(codeOf(game(), make('seat_moved', { from: 4, to: 2, correction: c }))).toBe(
        'invalid_payload',
      );
    },
  );

  it('a rejected uncorrected move leaves the state exactly as it was', () => {
    const before = game();
    expect(apply(before, moved(4, 2))).toEqual({ ok: false, error: { code: 'invalid_phase' } });
  });
});

// ---------------------------------------------------------------------------------------------
describe('criterion 3: settings_changed', () => {
  it('sets trust_mode to strict in the lobby', () => {
    expect(
      step(seated(2), ev('settings_changed', { trust_mode: 'strict' })).settings.trust_mode,
    ).toBe('strict');
  });

  it('sets it back to casual', () => {
    const strict = step(seated(2), ev('settings_changed', { trust_mode: 'strict' }));
    expect(step(strict, ev('settings_changed', { trust_mode: 'casual' })).settings.trust_mode).toBe(
      'casual',
    );
  });

  it.each([
    ['STRICT', 'STRICT'],
    ['Casual', 'Casual'],
    ['the empty string', ''],
    ['strict with a trailing space', 'strict '],
    ['null', null],
    ['the number 1', 1],
    ['true', true],
    ['an array', ['strict']],
    ['an object', {}],
    ['banana', 'banana'],
  ])('rejects trust_mode %s with invalid_setting', (_label, value) => {
    expect(codeOf(seated(2), ev('settings_changed', { trust_mode: value }))).toBe(
      'invalid_setting',
    );
  });

  it('a rejected trust_mode leaves the state untouched', () => {
    const before = seated(2);
    expect(apply(before, ev('settings_changed', { trust_mode: 'x' }))).toEqual({
      ok: false,
      error: { code: 'invalid_setting' },
    });
  });

  it('is invalid_phase after game_started, for a good value and for a bad one', () => {
    const game = activeGame(2);
    expect(codeOf(game, ev('settings_changed', { trust_mode: 'strict' }))).toBe('invalid_phase');
    expect(codeOf(game, ev('settings_changed', { trust_mode: 'casual' }))).toBe('invalid_phase');
    expect(codeOf(game, ev('settings_changed', { trust_mode: 'BANANA' }))).toBe('invalid_phase');
    expect(codeOf(game, ev('settings_changed', {}))).toBe('invalid_phase');
  });

  it.each(['ending', 'finished', 'abandoned'] as const)(
    'is invalid_phase in a %s game',
    (phase) => {
      expect(
        codeOf(inPhase(activeGame(2), phase), ev('settings_changed', { trust_mode: 'strict' })),
      ).toBe('invalid_phase');
    },
  );

  it('the strict mode set in the lobby is still the trust mode of the started game', () => {
    const lobby = step(seated(2), ev('settings_changed', { trust_mode: 'strict' }));
    expect(step(lobby, started(1)).settings.trust_mode).toBe('strict');
  });
});

// ---------------------------------------------------------------------------------------------
describe('criterion 4: table limits and the minimum to start', () => {
  it('rejects the 9th seat_joined with table_full, whichever (taken) id it names', () => {
    const full = seated(8);
    expect(full.seats).toHaveLength(8);
    for (let id = 1; id <= 8; id++) {
      expect(codeOf(full, joined(id))).toBe('table_full');
    }
  });

  it('with 8 seats an id outside 1 to 8 is invalid_payload, checked before table_full (4.1)', () => {
    // Pins the reading both reducers share: the shape is checked first (4.4.1).
    expect(codeOf(seated(8), joined(9))).toBe('invalid_payload');
  });

  it('accepts up to 8 seats, and a 7-seat table still gives seat_taken for a taken id', () => {
    expect(seated(8).seats).toHaveLength(8);
    expect(codeOf(seated(7), joined(4))).toBe('seat_taken');
    expect(codeOf(seated(7), joined(8))).toBe('ok');
  });

  it('a table that dropped from 8 to 7 accepts a seat again', () => {
    const seven = step(seated(8), ev('seat_left', { seat: 8 }));
    expect(codeOf(seven, joined(8))).toBe('ok');
  });

  it('game_started with 1 seat is not_enough_players', () => {
    expect(codeOf(seated(1), started(1))).toBe('not_enough_players');
  });

  it('game_started with 2 seats is accepted', () => {
    expect(codeOf(seated(2), started(2))).toBe('ok');
  });

  it('game_started with 0 seats is unknown_seat: first_seat is checked before the head count (4.4.9)', () => {
    // The criterion names only the 1-seat table. 4.4.9 states the order explicitly.
    expect(codeOf(lobbyState(), started(1))).toBe('unknown_seat');
  });

  it('a 1-seat table with a first_seat that is not that seat is unknown_seat (4.4.9)', () => {
    expect(codeOf(seated(1), started(2))).toBe('unknown_seat');
  });

  it('a seat that left no longer counts: 2 seats minus 1 is not_enough_players', () => {
    const one = step(seated(2), ev('seat_left', { seat: 2 }));
    expect(codeOf(one, started(1))).toBe('not_enough_players');
  });
});

// ---------------------------------------------------------------------------------------------
describe('criterion 5: game_started', () => {
  it.each([1, 2, 3, 4])('first_seat %i of 4: that seat is active, turn 1, round 1', (first) => {
    const after = step(seated(4), started(first));
    expect([after.phase, after.active_seat, after.first_seat, after.turn, after.round]).toEqual([
      'active',
      first,
      first,
      1,
      1,
    ]);
  });

  it('first_seat 8 of 8', () => {
    const after = step(seated(8), started(8));
    expect([after.active_seat, after.turn, after.round]).toEqual([8, 1, 1]);
  });

  it('puts every seat at the default starting life of 40', () => {
    expect(step(seated(4), started(3)).seats.map((s) => s.life)).toEqual([40, 40, 40, 40]);
  });

  it.each([1, 20, 100, 999])('puts every seat at a changed starting_life of %i', (life) => {
    const lobby = step(seated(3), ev('settings_changed', { starting_life: life }));
    expect(lobby.seats.map((s) => s.life)).toEqual([life, life, life]);
    expect(step(lobby, started(2)).seats.map((s) => s.life)).toEqual([life, life, life]);
  });

  it('a seat that joins after a starting_life change is at the new life', () => {
    const lobby = step(step(seated(2), ev('settings_changed', { starting_life: 25 })), joined(3));
    expect(step(lobby, started(1)).seats.map((s) => s.life)).toEqual([25, 25, 25]);
  });

  it('seats created from a game_created settings start at its starting_life', () => {
    const lobby = run(
      lobbyState(),
      ev('game_created', { settings: { ...SETTING_DEFAULTS, starting_life: 60 } }),
      joined(1),
      joined(2),
    );
    expect(step(lobby, started(2)).seats.map((s) => s.life)).toEqual([60, 60]);
  });

  it('a first_seat that is not at the table is unknown_seat', () => {
    expect(codeOf(seated(4), started(5))).toBe('unknown_seat');
    const gap = run(lobbyState(), joined(1), joined(2), joined(4));
    expect(codeOf(gap, started(3))).toBe('unknown_seat');
  });

  it('a started game cannot be started again', () => {
    expect(codeOf(activeGame(3), started(1))).toBe('invalid_phase');
  });

  it('first_seat is null in the lobby, set by the start, and never changed afterwards', () => {
    const lobby = seated(4);
    expect(lobby.first_seat).toBeNull();
    let s = step(lobby, started(3));
    expect(s.first_seat).toBe(3);
    s = step(s, moved(3, 1, true));
    s = step(s, ev('turn_passed'));
    s = step(s, ev('turn_passed'));
    expect(s.first_seat).toBe(3);
  });
});

// ---------------------------------------------------------------------------------------------
describe('criterion 6: turn_passed skips eliminated seats (hand-built states)', () => {
  /**
   * No event can eliminate a seat before S-6.1.3, so these states are built by hand. S-6.1.3 MUST
   * add shared vectors for these cases once elimination events exist.
   */
  const game = (
    order: number[],
    first: number,
    dead: number[],
    active: number,
    turn = 7,
    round = 2,
  ): GameState => ({
    ...withEliminated(activeGame(4, first), ...dead),
    order,
    first_seat: first,
    active_seat: active,
    turn,
    round,
  });
  const pass = (s: GameState) => step(s, ev('turn_passed'));
  const natural = [1, 2, 3, 4];

  // The criterion's literal example: seat 3 active, 4 seats, seat 4 eliminated. Seat 1 is next in
  // every case. The round goes up iff seat 1 is the round head: the first player F, or the next
  // surviving seat after F when F is out (for F = 4 that is seat 1).
  it.each([
    [1, 3],
    [2, 2],
    [3, 2],
    [4, 3],
  ])(
    'seat 3 active, seat 4 out, first player %i: seat 1 is next, turn 8, round %i',
    (first, round) => {
      const next = pass(game(natural, first, [4], 3));
      expect([next.active_seat, next.turn, next.round]).toEqual([1, 8, round]);
      expect(next.first_seat).toBe(first);
    },
  );

  // [label, order, first player, eliminated, active, next seat, round goes up]
  const cases: [string, number[], number, number[], number, number, boolean][] = [
    [
      'first player out between turns, next survivor is the round head',
      natural,
      2,
      [2],
      1,
      3,
      true,
    ],
    ['first player out on its own turn: the next pass starts a round', natural, 2, [2], 2, 3, true],
    ['first player out, play not yet at the next survivor', natural, 2, [2], 4, 1, false],
    [
      'first player 1 out, wrapping from the last seat to the next survivor',
      natural,
      1,
      [1],
      4,
      2,
      true,
    ],
    ['first player 1 out, mid-round', natural, 1, [1], 3, 4, false],
    ['two eliminated seats in a row are both skipped', natural, 1, [2, 3], 1, 4, false],
    ['two in a row, wrapping back to the first player', natural, 1, [2, 3], 4, 1, true],
    ['two in a row at the head, first player out too', natural, 1, [1, 2], 4, 3, true],
    ['two in a row at the head, mid-round', natural, 1, [1, 2], 3, 4, false],
    [
      'two eliminated at the end wrap to the head and start a round',
      natural,
      1,
      [3, 4],
      2,
      1,
      true,
    ],
    [
      'first player and the seat after it out at the end of the order',
      natural,
      3,
      [3, 4],
      2,
      1,
      true,
    ],
    [
      'wrap-around from the last seat past an eliminated head to the first player',
      natural,
      2,
      [1],
      4,
      2,
      true,
    ],
    ['a seat eliminated on its own turn is never chosen again', natural, 1, [3], 3, 4, false],
    [
      'order that is not the seat ids: 4 out, active 1 goes to 2',
      [3, 1, 4, 2],
      1,
      [4],
      1,
      2,
      false,
    ],
    ['order that is not the seat ids: wrap-around to 3', [3, 1, 4, 2], 1, [4], 2, 3, false],
    [
      'order that is not the seat ids: play reaches the first player 1',
      [3, 1, 4, 2],
      1,
      [4],
      3,
      1,
      true,
    ],
    [
      'one survivor that is the first player: every pass is a round',
      natural,
      3,
      [1, 2, 4],
      3,
      3,
      true,
    ],
    [
      'one survivor, the first player out: every pass is a round',
      natural,
      1,
      [1, 2, 4],
      3,
      3,
      true,
    ],
  ];
  it.each(cases)('%s', (_label, order, first, dead, active, next, roundUp) => {
    const before = game(order, first, dead, active);
    const after = pass(before);
    expect(after.active_seat).toBe(next);
    expect(after.turn).toBe(8);
    expect(after.round).toBe(roundUp ? 3 : 2);
    // nothing else moves
    expect(after.first_seat).toBe(first);
    expect(after.order).toEqual(order);
    expect(after.seats).toEqual(before.seats);
    expect(after.last_seq).toBe(before.last_seq + 1);
  });

  it('a lone survivor passes to itself and gains a round every time', () => {
    let s = game(natural, 1, [1, 2, 4], 3);
    for (let i = 1; i <= 3; i++) {
      s = pass(s);
      expect([s.active_seat, s.turn, s.round]).toEqual([3, 7 + i, 2 + i]);
    }
  });

  it('a full circuit with seat 2 out visits 1,3,4 and counts one round per circuit', () => {
    let s = game(natural, 1, [2], 1, 1, 1);
    const seen: number[] = [];
    for (let i = 0; i < 6; i++) {
      s = pass(s);
      seen.push(s.active_seat as number);
    }
    expect(seen).toEqual([3, 4, 1, 3, 4, 1]);
    expect([s.turn, s.round]).toEqual([7, 3]);
  });

  it('is invalid_phase when every seat is eliminated', () => {
    expect(codeOf(game(natural, 1, [1, 2, 3, 4], 1), ev('turn_passed'))).toBe('invalid_phase');
  });

  it('does not change the state it is given', () => {
    const before = game(natural, 1, [4], 3);
    const copy = structuredClone(before);
    pass(before);
    expect(before).toEqual(copy);
  });
});

// ---------------------------------------------------------------------------------------------
// Contract and code cannot drift on the nine handlers.
const HANDLERS = [...S_6_1_6_TYPES];

function between(start: RegExp, end: RegExp): string {
  const s = contract.search(start);
  if (s < 0) throw new Error(`CONTRACT.md has no ${start}`);
  const rest = contract.slice(s + 1);
  const e = rest.search(end);
  return e < 0 ? rest : rest.slice(0, e);
}

describe('contract 4.4 and the code agree on the nine handlers', () => {
  const sharedRules = between(/\n#### 4\.4\.1 /, /\n#{2,4} (?!4\.4\.1 )/);
  const allowed = new Map<string, string>();
  for (const m of sharedRules.matchAll(/^\| `([a-z_]+)` *\| (.+?) *\|$/gm))
    allowed.set(m[1]!, m[2]!);

  it('the "Allowed when" table lists exactly the nine handlers', () => {
    expect([...allowed.keys()].sort()).toEqual([...HANDLERS].sort());
  });

  const validEvent: Record<string, (correction: boolean) => EventEnvelope> = {
    game_created: () => ev('game_created', { settings: { ...SETTING_DEFAULTS } }),
    settings_changed: () => ev('settings_changed', { trust_mode: 'strict' }),
    seat_joined: () => joined(7),
    seat_left: () => ev('seat_left', { seat: 3 }),
    seat_moved: (c) => moved(1, 3, c),
    deck_assigned: () =>
      ev('deck_assigned', {
        seat: 1,
        deck_version_id: 'd',
        commanders: [{ card_id: null, name: 'C' }],
      }),
    commanders_set: () =>
      ev('commanders_set', { seat: 1, commanders: [{ card_id: 'c', name: 'C' }] }),
    game_started: () => started(1),
    turn_passed: () => ev('turn_passed'),
  };
  const PHASES: Phase[] = ['lobby', 'active', 'ending', 'finished', 'abandoned'];
  const stateIn = (phase: Phase): GameState =>
    phase === 'lobby' ? seated(3) : inPhase(activeGame(3), phase);

  it.each(HANDLERS.filter((h) => h !== 'game_created'))(
    '%s is accepted in exactly the phases the table allows',
    (handler) => {
      const text = allowed.get(handler) ?? '';
      const named = [...text.matchAll(/`phase` is `([a-z]+)`/g)].map((m) => m[1]);
      expect(named.length).toBeGreaterThan(0);
      const needsCorrection = text.includes('`correction: true`');
      for (const phase of PHASES) {
        for (const correction of [true, false]) {
          const listed = named.includes(phase);
          const expected =
            listed && (!needsCorrection || phase !== 'active' || correction)
              ? 'ok'
              : 'invalid_phase';
          expect(
            codeOf(stateIn(phase), validEvent[handler]!(correction)),
            `${handler} in ${phase} with correction ${correction}`,
          ).toBe(expected);
        }
      }
    },
  );

  it('game_created is accepted exactly when last_seq is 0, in any phase', () => {
    expect(allowed.get('game_created')).toContain('`state.last_seq` is 0');
    for (const phase of PHASES) {
      expect(codeOf(inPhase(lobbyState(), phase), validEvent.game_created!(true))).toBe('ok');
      expect(codeOf({ ...stateIn(phase), last_seq: 1 }, validEvent.game_created!(true))).toBe(
        'invalid_phase',
      );
    }
  });

  // The error codes each subsection lists, read from the document.
  const codesOf = new Map<string, Set<string>>();
  for (const m of contract.matchAll(/\n#### 4\.4\.\d+ `([a-z_]+)`\n/g)) {
    const body = between(new RegExp(`\\n#### 4\\.4\\.\\d+ \`${m[1]}\`\\n`), /\n#{2,4} /);
    const codes = new Set<string>();
    for (const line of body.match(/^\d+\. .*$/gm) ?? []) {
      for (const span of line.matchAll(/`([a-z_]+)`/g)) {
        if ((REDUCER_ERROR_CODES as readonly string[]).includes(span[1]!)) codes.add(span[1]!);
      }
    }
    codesOf.set(m[1]!, codes);
  }

  it('commanders_set takes its checks 1 to 3 from deck_assigned, as the document says', () => {
    expect(between(/\n#### 4\.4\.8 /, /\n#{2,4} /)).toContain(
      'Checks 1 to 3 are those of `deck_assigned`',
    );
  });

  codesOf.set('commanders_set', codesOf.get('deck_assigned')!);

  const LISTED: Record<string, string[]> = {
    game_created: ['invalid_payload', 'invalid_phase', 'invalid_setting'],
    settings_changed: ['invalid_phase', 'invalid_setting'],
    seat_joined: ['invalid_payload', 'invalid_phase', 'seat_taken', 'table_full'],
    seat_left: ['invalid_payload', 'invalid_phase', 'unknown_seat'],
    seat_moved: ['invalid_payload', 'invalid_phase', 'unknown_seat'],
    deck_assigned: ['invalid_payload', 'invalid_phase', 'unknown_seat'],
    commanders_set: ['invalid_payload', 'invalid_phase', 'unknown_seat'],
    game_started: ['invalid_payload', 'invalid_phase', 'not_enough_players', 'unknown_seat'],
    turn_passed: ['invalid_phase'],
  };

  // One probe per listed code: a state and an event that must produce exactly that code.
  const probes: Record<string, Record<string, [GameState, EventEnvelope]>> = {
    game_created: {
      invalid_payload: [lobbyState(), ev('game_created', { settings: {} })],
      invalid_phase: [seated(1), ev('game_created', { settings: { ...SETTING_DEFAULTS } })],
      invalid_setting: [
        lobbyState(),
        ev('game_created', { settings: { ...SETTING_DEFAULTS, poison_threshold: 100 } }),
      ],
    },
    settings_changed: {
      invalid_phase: [activeGame(3), ev('settings_changed', { trust_mode: 'strict' })],
      invalid_setting: [seated(3), ev('settings_changed', { starting_life: 0 })],
    },
    seat_joined: {
      invalid_payload: [seated(3), joined(0)],
      invalid_phase: [activeGame(3), joined(7)],
      seat_taken: [seated(3), joined(2)],
      table_full: [seated(8), joined(3)],
    },
    seat_left: {
      invalid_payload: [seated(3), ev('seat_left', {})],
      invalid_phase: [activeGame(3), ev('seat_left', { seat: 1 })],
      unknown_seat: [seated(3), ev('seat_left', { seat: 5 })],
    },
    seat_moved: {
      invalid_payload: [seated(3), ev('seat_moved', { from: 1 })],
      invalid_phase: [activeGame(3), moved(1, 2)],
      unknown_seat: [seated(3), moved(1, 5)],
    },
    deck_assigned: {
      invalid_payload: [seated(3), ev('deck_assigned', {})],
      invalid_phase: [activeGame(3), validEvent.deck_assigned!(true)],
      unknown_seat: [
        seated(3),
        ev('deck_assigned', {
          seat: 5,
          deck_version_id: 'd',
          commanders: [{ card_id: null, name: 'C' }],
        }),
      ],
    },
    commanders_set: {
      invalid_payload: [seated(3), ev('commanders_set', {})],
      invalid_phase: [activeGame(3), validEvent.commanders_set!(true)],
      unknown_seat: [
        seated(3),
        ev('commanders_set', { seat: 5, commanders: [{ card_id: null, name: '' }] }),
      ],
    },
    game_started: {
      invalid_payload: [seated(3), ev('game_started', {})],
      invalid_phase: [activeGame(3), started(1)],
      unknown_seat: [seated(3), started(5)],
      not_enough_players: [seated(1), started(1)],
    },
    turn_passed: { invalid_phase: [seated(3), ev('turn_passed')] },
  };

  it.each(HANDLERS)(
    '%s: the codes the document lists are the codes the code returns',
    (handler) => {
      const listed = [...(codesOf.get(handler) ?? [])].sort();
      expect(listed).toEqual([...LISTED[handler]!].sort());
      expect(Object.keys(probes[handler]!).sort()).toEqual(listed);
      for (const [code, [state, event]] of Object.entries(probes[handler]!)) {
        expect(codeOf(state, event), `${handler} -> ${code}`).toBe(code);
      }
    },
  );

  it('first_seat is a documented state key, null in an empty lobby', () => {
    expect(contract).toMatch(/^\| `first_seat` +\| integer or null /m);
    expect(lobbyState().first_seat).toBeNull();
  });
});

// ---------------------------------------------------------------------------------------------
describe('vector coverage of the nine handlers (8.4.1)', () => {
  const dir = new URL('../vectors/', import.meta.url);
  const vectors = readdirSync(dir)
    .filter((f) => f.endsWith('.json'))
    .map((f) => JSON.parse(readFileSync(new URL(f, dir), 'utf8')) as Record<string, unknown>)
    .filter((v) => v.kind === 'reducer') as {
    events: { type: unknown }[];
    expect: { state?: unknown; error?: { index: number } };
  }[];

  const applied = (v: (typeof vectors)[number]) =>
    v.events.slice(0, v.expect.error ? v.expect.error.index : v.events.length).map((e) => e.type);

  it('every one of the 29 registered types is applied successfully by some vector', () => {
    const seen = new Set(vectors.flatMap(applied));
    expect(EVENT_TYPES.filter((t) => !seen.has(t))).toEqual([]);
  });

  it.each(S_6_1_6_TYPES)('%s appears in at least one vector that expects a state', (type) => {
    const inState = vectors.filter((v) => v.expect.state !== undefined).flatMap(applied);
    expect(inState).toContain(type);
  });

  it('every handler rejection code of the nine appears in at least one reject vector', () => {
    const seen = new Set<string>();
    for (const v of vectors) {
      const e = v.expect.error as { code?: string } | undefined;
      if (e?.code) seen.add(e.code);
    }
    for (const code of [
      'invalid_phase',
      'invalid_setting',
      'invalid_payload',
      'unknown_seat',
      'seat_taken',
      'table_full',
      'not_enough_players',
    ]) {
      expect(seen.has(code), code).toBe(true);
    }
  });

  it('the types that are still no-ops are exactly the 20 that are not S-6.1.6 types', () => {
    expect(NO_OP_TYPES).toHaveLength(20);
    expect(S_6_1_6_TYPES).toHaveLength(9);
  });
});
