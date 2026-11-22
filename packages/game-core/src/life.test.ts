import { describe, expect, it } from 'vitest';
import { canonicalJson } from './canonical-json';
import { activeGame, after, codeOf, make, run, seated, withEliminated } from './lobby.test.helpers';
import { apply } from './reducer';
import { PHASES, WIN_CONDITIONS } from './types';
import type { GameState, Phase } from './types';

/**
 * S-6.1.3 handlers (CONTRACT.md 4.5): life_changed, player_conceded and player_eliminated. The
 * shared vectors pin everything reachable through events; this file adds what a vector cannot say:
 * the `ending` phase and the closed phases (no event reaches them yet), and the link between the
 * criteria and the code.
 */

const inPhase = (state: GameState, phase: Phase): GameState => ({
  ...state,
  phase,
  resume_phase: phase === 'abandoned' ? 'active' : null,
});
const life = (seat: unknown, delta: unknown, source?: unknown) =>
  make(
    'life_changed',
    source === undefined ? { seat, delta } : { seat, delta, source_seat: source },
  );
const concede = (seat: unknown) => make('player_conceded', { seat });
const declare = (seat: unknown, cause?: unknown) =>
  make('player_eliminated', cause === undefined ? { seat } : { seat, cause });
const seat = (state: GameState, n: number) => state.seats.find((s) => s.seat === n)!;
const withLife = (state: GameState, n: number, value: unknown): GameState => ({
  ...state,
  seats: state.seats.map((s) => (s.seat === n ? { ...s, life: value as number } : s)),
});

describe('criterion 1: starting life', () => {
  it('gives every seat 40 life with the default settings', () => {
    expect(activeGame(4).seats.map((s) => s.life)).toEqual([40, 40, 40, 40]);
  });

  it('gives every seat 30 life with starting_life 30', () => {
    const state = run(
      seated(3, { starting_life: 30 }),
      make('game_started', { first_seat: 1, started_at: 'x' }),
    );
    expect(state.seats.map((s) => s.life)).toEqual([30, 30, 30]);
  });
});

describe('criteria 2 and 3: life_changed and the life boundary', () => {
  it('eliminates a seat at exactly 0 life, with the source and the event seq (criterion 2)', () => {
    const state = withLife(activeGame(4), 3, 3);
    const next = after(state, life(3, -3, 1));
    expect(seat(next, 3)).toMatchObject({
      life: 0,
      elimination_reason: 'life',
      eliminated_by: 1,
      eliminated_at_seq: state.last_seq + 1,
      eliminated_round: 1,
      elimination_cause: null,
    });
  });

  it('records -3 for a seat at 5 life with delta -8, not clamped, with no eliminator (criterion 3)', () => {
    const next = after(withLife(activeGame(4), 2, 5), life(2, -8));
    expect(seat(next, 2)).toMatchObject({
      life: -3,
      elimination_reason: 'life',
      eliminated_by: null,
    });
  });

  it('keeps a seat at 1 life in the game', () => {
    const next = after(withLife(activeGame(2), 1, 5), life(1, -4));
    expect(seat(next, 1)).toMatchObject({
      life: 1,
      eliminated_at_seq: null,
      elimination_reason: null,
    });
  });

  it('records the seq an optimistic event is applied with (last_seq + 1)', () => {
    const state = activeGame(3);
    const next = after(state, life(1, -40));
    expect(seat(next, 1).eliminated_at_seq).toBe(state.last_seq + 1);
    expect(next.last_seq).toBe(state.last_seq + 1);
  });

  it('uses the confirmed seq when the event has one', () => {
    const state = activeGame(3);
    const next = after(
      state,
      make('life_changed', { seat: 1, delta: -40 }, { seq: state.last_seq + 1 }),
    );
    expect(seat(next, 1).eliminated_at_seq).toBe(state.last_seq + 1);
  });

  it('takes eliminated_by from source_seat, also when it is the seat itself', () => {
    expect(seat(after(activeGame(3), life(2, -40, 2)), 2).eliminated_by).toBe(2);
    expect(seat(after(activeGame(3), life(2, -40, null)), 2).eliminated_by).toBe(null);
  });

  it('never eliminates on a positive delta, even from a life at or below 0', () => {
    const state = withLife(activeGame(2), 1, -5);
    const next = after(state, life(1, 3));
    expect(seat(next, 1)).toMatchObject({ life: -2, eliminated_at_seq: null });
    const zero = after(withLife(activeGame(2), 1, 0), life(1, 1, 2));
    expect(seat(zero, 1).eliminated_at_seq).toBe(null);
  });

  it('changes only the seat life, the seq and the elimination fields', () => {
    const state = activeGame(4);
    const next = after(state, life(2, -7, 3));
    expect({ ...next, seats: state.seats, last_seq: state.last_seq }).toEqual(state);
    expect(next.seats.filter((s) => s.seat !== 2)).toEqual(state.seats.filter((s) => s.seat !== 2));
  });

  it('leaves the active seat, the turn and the order alone when the active seat is eliminated', () => {
    const state = activeGame(3, 2);
    const next = after(state, life(2, -40));
    expect([next.active_seat, next.turn, next.round, next.order]).toEqual([2, 1, 1, [1, 2, 3]]);
  });
});

describe('criterion 5: invalid amounts and causes', () => {
  it.each([0, 1000, -1000, 99999])('delta %i is invalid_amount', (delta) => {
    expect(codeOf(activeGame(3), life(1, delta))).toBe('invalid_amount');
  });

  it.each([999, -999, 1, -1])('delta %i is accepted', (delta) => {
    expect(codeOf(activeGame(3), life(1, delta))).toBe('ok');
  });

  it.each(['3', null, true, 1.5, [], {}, undefined])('delta %j is invalid_payload', (delta) => {
    expect(codeOf(activeGame(3), life(1, delta))).toBe('invalid_payload');
  });

  it('accepts every D5 code as a cause and records it', () => {
    for (const cause of WIN_CONDITIONS) {
      const next = after(activeGame(3), declare(2, cause));
      expect(seat(next, 2)).toMatchObject({
        elimination_reason: 'declared',
        elimination_cause: cause,
      });
    }
  });

  it.each(['draw', 'Mill', '', 'mill ', 7, true, false, [], {}])(
    'cause %j is invalid_payload',
    (cause) => {
      expect(codeOf(activeGame(3), declare(2, cause))).toBe('invalid_payload');
    },
  );
});

describe('a life that is not an integer (4.5.1)', () => {
  const values: [string, unknown][] = [
    ['a string', '40'],
    ['null', null],
    ['true', true],
    ['false', false],
    ['an object', {}],
    ['an array', [40]],
    ['a fraction', 40.5],
    ['NaN', Number.NaN],
    ['infinity', Number.POSITIVE_INFINITY],
    ['a number beyond the safe range', 2 ** 60],
  ];

  it.each(values)(
    'life_changed on a life that is %s is invalid_amount, and never throws',
    (_name, value) => {
      const state = withLife(activeGame(3), 1, value);
      expect(codeOf(state, life(1, -3))).toBe('invalid_amount');
      expect(codeOf(state, life(1, 3))).toBe('invalid_amount');
    },
  );

  it('does not read the life of another seat', () => {
    expect(codeOf(withLife(activeGame(3), 2, '40'), life(1, -3))).toBe('ok');
  });

  it.each(values)(
    'a concession or a declared elimination works on a life that is %s',
    (_name, value) => {
      const state = withLife(activeGame(3), 1, value);
      expect(codeOf(state, concede(1))).toBe('ok');
      expect(codeOf(state, declare(1, 'mill'))).toBe('ok');
    },
  );

  it('is invalid_amount when the new life leaves the safe range', () => {
    const max = Number.MAX_SAFE_INTEGER;
    expect(codeOf(withLife(activeGame(2), 1, max), life(1, 1))).toBe('invalid_amount');
    expect(codeOf(withLife(activeGame(2), 1, max - 1), life(1, 1))).toBe('ok');
    expect(codeOf(withLife(activeGame(2), 1, -max), life(1, -1))).toBe('invalid_amount');
    expect(codeOf(withLife(activeGame(2), 1, -max + 1), life(1, -1))).toBe('ok');
  });
});

describe('criterion 4: events on an eliminated seat', () => {
  const dead = () => after(activeGame(4), concede(2));

  it('rejects life_changed, player_conceded and player_eliminated on it with seat_eliminated', () => {
    expect(codeOf(dead(), life(2, -1))).toBe('seat_eliminated');
    expect(codeOf(dead(), life(2, 1))).toBe('seat_eliminated');
    expect(codeOf(dead(), concede(2))).toBe('seat_eliminated');
    expect(codeOf(dead(), declare(2))).toBe('seat_eliminated');
  });

  it('rejects an eliminated source_seat, also on a gain', () => {
    expect(codeOf(dead(), life(1, -1, 2))).toBe('seat_eliminated');
    expect(codeOf(dead(), life(1, 1, 2))).toBe('seat_eliminated');
  });

  it('is checked after unknown_seat and before invalid_amount', () => {
    expect(codeOf(dead(), life(2, 0, 7))).toBe('unknown_seat');
    expect(codeOf(dead(), life(2, 0))).toBe('seat_eliminated');
    expect(codeOf(dead(), life(1, 0, 2))).toBe('seat_eliminated');
  });

  it('applies to a seat eliminated by life or by a hand-built state alike', () => {
    expect(codeOf(after(activeGame(3), life(1, -40)), life(1, -1))).toBe('seat_eliminated');
    expect(codeOf(withEliminated(activeGame(3), 1), concede(1))).toBe('seat_eliminated');
  });

  it('still lets a host correction move an eliminated seat in the order', () => {
    const next = after(dead(), make('seat_moved', { from: 2, to: 4, correction: true }));
    expect(next.order).toEqual([1, 4, 3, 2]);
    expect(seat(next, 2).eliminated_at_seq).not.toBe(null);
  });

  it('cannot be reached by an S-6.1.6 handler: they all need the lobby in an active game', () => {
    const state = dead();
    const lobbyOnly = [
      make('settings_changed', { starting_life: 20 }),
      make('seat_joined', { seat: 2, player_id: null, name: 'x' }),
      make('seat_left', { seat: 2 }),
      make('deck_assigned', {
        seat: 2,
        deck_version_id: 'd',
        commanders: [{ card_id: null, name: '' }],
      }),
      make('commanders_set', { seat: 2, commanders: [{ card_id: null, name: '' }] }),
      make('game_started', { first_seat: 2, started_at: 'x' }),
      make('seat_moved', { from: 2, to: 1 }),
    ];
    for (const phase of ['active', 'ending'] as const) {
      for (const e of lobbyOnly) expect(codeOf(inPhase(state, phase), e)).toBe('invalid_phase');
    }
  });
});

describe('phases', () => {
  const events = [
    ['life_changed', () => life(1, -1)],
    ['player_conceded', () => concede(1)],
    ['player_eliminated', () => declare(1)],
  ] as const;

  it.each(events)(
    '%s is accepted in active and in ending, and is invalid_phase elsewhere',
    (_t, build) => {
      for (const phase of PHASES) {
        const expected = phase === 'active' || phase === 'ending' ? 'ok' : 'invalid_phase';
        expect(codeOf(inPhase(activeGame(3), phase), build()), phase).toBe(expected);
      }
    },
  );

  it('gives the same result in ending as in active', () => {
    const e = life(2, -41, 1);
    const a = apply(activeGame(3), e);
    const b = apply(inPhase(activeGame(3), 'ending'), e);
    expect(a.ok && b.ok && canonicalJson({ ...a.state, phase: 'ending' })).toBe(
      b.ok && canonicalJson(b.state),
    );
  });

  it('does not move the phase when every seat is eliminated', () => {
    const next = after(after(activeGame(2), concede(1)), concede(2));
    expect([next.phase, next.no_winner, next.winner_seat, next.win_condition]).toEqual([
      'active',
      true,
      null,
      null,
    ]);
  });
});

describe('check order (4.5.1)', () => {
  it('payload shape, then phase, then unknown_seat, then seat_eliminated, then the amount', () => {
    const lobby = seated(3);
    expect(codeOf(lobby, life(1, 'x'))).toBe('invalid_payload');
    expect(codeOf(lobby, life(9, -1))).toBe('invalid_payload');
    expect(codeOf(lobby, life(5, -1))).toBe('invalid_phase');
    expect(codeOf(activeGame(3), life(5, 0))).toBe('unknown_seat');
    expect(codeOf(activeGame(3), life(1, -1, 5))).toBe('unknown_seat');
    expect(codeOf(activeGame(3), life(1, 0))).toBe('invalid_amount');
  });

  it('ignores unknown payload keys, and reads only own keys', () => {
    const inherited = make(
      'player_conceded',
      Object.create({ seat: 1 }) as Record<string, unknown>,
    );
    expect(codeOf(activeGame(3), inherited)).toBe('invalid_payload');
    expect(codeOf(activeGame(3), make('life_changed', { seat: 1, delta: -1, x: 1 }))).toBe('ok');
  });

  it('a rejected event changes nothing', () => {
    const state = activeGame(3);
    const copy = canonicalJson(state);
    apply(state, life(1, 0));
    expect(canonicalJson(state)).toBe(copy);
  });
});
