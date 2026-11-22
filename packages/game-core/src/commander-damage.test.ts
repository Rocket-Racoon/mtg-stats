import { describe, expect, it } from 'vitest';
import { canonicalJson } from './canonical-json';
import { activeGame, after, codeOf, make, run, withEliminated } from './lobby.test.helpers';
import { apply } from './reducer';
import { PHASES } from './types';
import type { GameState, Phase } from './types';

/**
 * S-6.1.4 handler (CONTRACT.md 4.6): commander_damage. The shared vectors pin everything reachable
 * through events; this file adds what a vector cannot say: the `ending` and the closed phases
 * (no event reaches them yet), hand-built states (unvalidated numbers, the safe range), purity of
 * the input, and the link between the criteria and the code.
 */

const cd = (source: unknown, commander: unknown, target: unknown, amount: unknown) =>
  make('commander_damage', {
    source_seat: source,
    commander,
    target_seat: target,
    amount,
  });
const inPhase = (state: GameState, phase: Phase): GameState => ({
  ...state,
  phase,
  resume_phase: phase === 'abandoned' ? 'active' : null,
});
const seat = (state: GameState, n: number) => state.seats.find((s) => s.seat === n)!;
const withSeat = (state: GameState, n: number, change: Record<string, unknown>): GameState => ({
  ...state,
  seats: state.seats.map((s) => (s.seat === n ? ({ ...s, ...change } as typeof s) : s)),
});
const withPartners = (state: GameState, n: number): GameState =>
  withSeat(state, n, {
    commanders: [
      { card_id: null, name: 'A' },
      { card_id: null, name: 'B' },
    ],
    commander_cast: [0, 0],
  });
const canon = (state: GameState) => canonicalJson(state);

describe('criteria 1 to 4 and 6 of S-6.1.4, with a hand-derived expectation', () => {
  it('three hits of 7 from 1:0 give 21, 19 life and an elimination (criterion 1)', () => {
    const state = run(activeGame(4), cd(1, 0, 3, 7), cd(1, 0, 3, 7), cd(1, 0, 3, 7));
    expect(state.commander_damage).toEqual({ '3': { '1:0': 21 } });
    expect(seat(state, 3)).toMatchObject({
      life: 19,
      elimination_reason: 'commander_damage',
      eliminated_by: 1,
      eliminated_at_seq: 8,
      eliminated_round: 1,
      elimination_cause: null,
    });
    expect(state.last_seq).toBe(8);
  });

  it('partners 12 + 12 are two tallies and 16 life, and nobody is eliminated (criterion 2)', () => {
    const state = run(withPartners(activeGame(4), 2), cd(2, 0, 4, 12), cd(2, 1, 4, 12));
    expect(state.commander_damage).toEqual({ '4': { '2:0': 12, '2:1': 12 } });
    expect(seat(state, 4).life).toBe(16);
    expect(seat(state, 4).eliminated_at_seq).toBeNull();
  });

  it('changes the tally, the life and the elimination in the one event (criterion 3)', () => {
    const before = run(activeGame(4), cd(1, 0, 3, 14));
    const result = apply(before, cd(1, 0, 3, 7));
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(before.commander_damage).toEqual({ '3': { '1:0': 14 } });
    expect(seat(before, 3)).toMatchObject({ life: 26, eliminated_at_seq: null });
    expect(result.state.commander_damage).toEqual({ '3': { '1:0': 21 } });
    expect(seat(result.state, 3)).toMatchObject({ life: 19, eliminated_at_seq: 7 });
  });

  it('commander_damage wins when the tally and the life both qualify (criterion 4)', () => {
    const before = withSeat(activeGame(4), 4, { life: 21 });
    const state = run(before, cd(1, 0, 4, 21));
    expect(seat(state, 4)).toMatchObject({
      life: 0,
      elimination_reason: 'commander_damage',
      eliminated_by: 1,
    });
  });

  it('reads the threshold from the settings (criterion 6)', () => {
    const before = { ...activeGame(3), settings: { ...activeGame(3).settings } };
    before.settings.commander_damage_threshold = 15;
    const two = run(before, cd(1, 0, 2, 7), cd(1, 0, 2, 7));
    expect(seat(two, 2).eliminated_at_seq).toBeNull();
    const three = run(two, cd(1, 0, 2, 1));
    expect(seat(three, 2)).toMatchObject({ elimination_reason: 'commander_damage', life: 25 });
  });
});

describe('the life-only elimination and the reason precedence', () => {
  it('eliminates by life, with the source, when the tally stays below the threshold', () => {
    const state = run(withSeat(activeGame(3), 2, { life: 5 }), cd(1, 0, 2, 5));
    expect(seat(state, 2)).toMatchObject({
      life: 0,
      elimination_reason: 'life',
      eliminated_by: 1,
    });
    expect(state.commander_damage).toEqual({ '2': { '1:0': 5 } });
  });

  it('uses only the tally of the one source key, never a sum over the partners', () => {
    const before = withPartners(activeGame(3), 1);
    const state = run(before, cd(1, 0, 2, 20), cd(1, 1, 2, 20));
    expect(seat(state, 2)).toMatchObject({ life: 0, elimination_reason: 'life', eliminated_by: 1 });
    expect(state.commander_damage).toEqual({ '2': { '1:0': 20, '1:1': 20 } });
  });

  it('a seat already at 0 life or less that is still in the game (unvalidated) is eliminated by life', () => {
    const state = run(withSeat(activeGame(3), 2, { life: 0 }), cd(1, 0, 2, 1));
    expect(seat(state, 2)).toMatchObject({
      life: -1,
      elimination_reason: 'life',
      eliminated_by: 1,
    });
  });
});

describe('self damage', () => {
  it('is legal and names the seat itself as the eliminator', () => {
    const state = run(activeGame(3), cd(2, 0, 2, 21));
    expect(seat(state, 2)).toMatchObject({
      life: 19,
      elimination_reason: 'commander_damage',
      eliminated_by: 2,
    });
    expect(state.commander_damage).toEqual({ '2': { '2:0': 21 } });
  });
});

describe('phases', () => {
  it('is allowed in the ending phase', () => {
    const state = run(inPhase(activeGame(3), 'ending'), cd(1, 0, 2, 3));
    expect(state.phase).toBe('ending');
    expect(seat(state, 2).life).toBe(37);
  });

  it.each(PHASES.filter((p) => p !== 'active' && p !== 'ending'))(
    'is invalid_phase in a %s game, even with a bad amount or an unknown seat',
    (phase) => {
      const state = inPhase(activeGame(3), phase);
      expect(codeOf(state, cd(1, 0, 2, 1))).toBe('invalid_phase');
      expect(codeOf(state, cd(1, 0, 2, 0))).toBe('invalid_phase');
      expect(codeOf(state, cd(1, 0, 7, 1))).toBe('invalid_phase');
    },
  );

  it('checks the payload shape before the phase', () => {
    expect(codeOf(inPhase(activeGame(3), 'finished'), cd(1, 0, 2, '1'))).toBe('invalid_payload');
  });
});

describe('check order', () => {
  const game = () => withPartners(activeGame(4), 2);

  it('goes shape, phase, unknown_seat, seat_eliminated, amount, commander, life', () => {
    const gone = withEliminated(game(), 3);
    expect(codeOf(gone, cd(1, 9, 2, 'x'))).toBe('invalid_payload');
    expect(codeOf(gone, cd(7, 5, 3, 0))).toBe('unknown_seat');
    expect(codeOf(gone, cd(1, 5, 3, 0))).toBe('seat_eliminated');
    expect(codeOf(gone, cd(3, 5, 1, 0))).toBe('seat_eliminated');
    expect(codeOf(game(), cd(1, 5, 2, 0))).toBe('invalid_amount');
    expect(codeOf(withSeat(game(), 2, { life: 'x' }), cd(1, 5, 2, 1))).toBe('unknown_commander');
    expect(codeOf(withSeat(game(), 2, { life: 'x' }), cd(1, 0, 2, 1))).toBe('invalid_amount');
  });

  it.each([
    [0, 'invalid_amount'],
    [-1, 'invalid_amount'],
    [1, 'ok'],
    [999, 'ok'],
    [1000, 'invalid_amount'],
    [-999, 'invalid_amount'],
  ])('amount %s is %s', (amount, code) => {
    expect(codeOf(activeGame(3), cd(1, 0, 2, amount))).toBe(code);
  });

  it('accepts commander indexes 0 and 1 of a partner seat, and rejects 2 and -1', () => {
    expect(codeOf(game(), cd(2, 0, 1, 1))).toBe('ok');
    expect(codeOf(game(), cd(2, 1, 1, 1))).toBe('ok');
    expect(codeOf(game(), cd(2, 2, 1, 1))).toBe('unknown_commander');
    expect(codeOf(game(), cd(2, -1, 1, 1))).toBe('unknown_commander');
  });

  it('does not accept a boolean, a string or a fraction for the commander or the amount', () => {
    for (const bad of [true, '0', 0.5, null, undefined]) {
      expect(codeOf(game(), cd(1, bad, 2, 1))).toBe('invalid_payload');
      expect(codeOf(game(), cd(1, 0, 2, bad))).toBe('invalid_payload');
    }
  });
});

describe('unvalidated numbers (3.5) and the safe range', () => {
  it.each([['40'], [true], [null], [1.5], [Number.NaN], [[]], [{}]])(
    'a life of %j is invalid_amount',
    (value) => {
      expect(codeOf(withSeat(activeGame(3), 2, { life: value }), cd(1, 0, 2, 1))).toBe(
        'invalid_amount',
      );
    },
  );

  it.each([['21'], [true], [null], [21.5], [Number.NaN], [Number.POSITIVE_INFINITY]])(
    'a threshold of %j is invalid_amount',
    (value) => {
      const state = activeGame(3);
      const broken = {
        ...state,
        settings: { ...state.settings, commander_damage_threshold: value },
      };
      expect(codeOf(broken as unknown as GameState, cd(1, 0, 2, 1))).toBe('invalid_amount');
    },
  );

  it.each([[0], [-5], [100], [1000]])('an integer threshold of %i is used as given', (value) => {
    const state = activeGame(3);
    const odd = { ...state, settings: { ...state.settings, commander_damage_threshold: value } };
    const next = run(odd, cd(1, 0, 2, 1));
    expect(seat(next, 2).eliminated_at_seq !== null).toBe(1 >= value);
  });

  it('rejects a life that would leave the safe range, and accepts the last one inside it', () => {
    const edge = -(2 ** 53 - 1);
    expect(codeOf(withSeat(activeGame(3), 2, { life: edge }), cd(1, 0, 2, 1))).toBe(
      'invalid_amount',
    );
    expect(codeOf(withSeat(activeGame(3), 2, { life: edge + 5 }), cd(1, 0, 2, 5))).toBe('ok');
  });

  it('rejects a tally that would leave the safe range', () => {
    const state = { ...activeGame(3), commander_damage: { '2': { '1:0': 2 ** 53 - 1 } } };
    expect(codeOf(state, cd(1, 0, 2, 1))).toBe('invalid_amount');
  });
});

describe('the state', () => {
  it('creates the target row and the source key on the first damage, and never removes them', () => {
    const start = activeGame(3);
    expect(start.commander_damage).toEqual({});
    const one = run(start, cd(1, 0, 2, 4));
    expect(one.commander_damage).toEqual({ '2': { '1:0': 4 } });
    const gained = run(one, make('life_changed', { seat: 2, delta: 10 }));
    expect(gained.commander_damage).toEqual({ '2': { '1:0': 4 } });
    const two = run(gained, cd(3, 0, 1, 2), cd(1, 0, 2, 1));
    expect(two.commander_damage).toEqual({ '1': { '3:0': 2 }, '2': { '1:0': 5 } });
  });

  it('writes the keys in canonical order whatever the order of the damage', () => {
    const state = run(
      withPartners(activeGame(4), 4),
      cd(4, 1, 3, 2),
      cd(3, 0, 1, 3),
      cd(2, 0, 4, 4),
      cd(4, 0, 3, 6),
      cd(1, 0, 4, 7),
    );
    expect(canonicalJson(state.commander_damage)).toBe(
      '{"1":{"3:0":3},"3":{"4:0":6,"4:1":2},"4":{"1:0":7,"2:0":4}}',
    );
  });

  it('changes only the target seat, its row, the elimination fields and last_seq', () => {
    const before = activeGame(4);
    const next = after(before, cd(1, 0, 3, 21));
    expect(next.seats.filter((s) => s.seat !== 3)).toEqual(
      before.seats.filter((s) => s.seat !== 3),
    );
    expect({ ...next, seats: [], commander_damage: {}, last_seq: 0, no_winner: false }).toEqual({
      ...before,
      seats: [],
      commander_damage: {},
      last_seq: 0,
      no_winner: false,
    });
  });

  it('never mutates the input state', () => {
    const before = run(activeGame(4), cd(1, 0, 3, 7));
    const snapshot = canon(before);
    run(before, cd(1, 0, 3, 7), cd(1, 0, 3, 7));
    expect(canon(before)).toBe(snapshot);
  });

  it('a rejected event changes nothing', () => {
    const before = run(activeGame(4), cd(1, 0, 3, 7));
    const snapshot = canon(before);
    expect(apply(before, cd(1, 1, 3, 7))).toEqual({
      ok: false,
      error: { code: 'unknown_commander' },
    });
    expect(canon(before)).toBe(snapshot);
  });

  it('does not set no_winner when other seats stay in the game, and sets it for the last seat', () => {
    expect(run(activeGame(3), cd(1, 0, 2, 21)).no_winner).toBe(false);
    const last = run(withEliminated(activeGame(3), 1, 3), cd(2, 0, 2, 21));
    expect(last.no_winner).toBe(true);
    expect(seat(last, 2).eliminated_at_seq).toBe(last.last_seq);
  });
});
