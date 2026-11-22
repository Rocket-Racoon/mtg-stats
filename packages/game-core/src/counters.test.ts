import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { canonicalJson } from './canonical-json';
import { activeGame, after, codeOf, make, run } from './lobby.test.helpers';
import { apply } from './reducer';
import { PHASES, REDUCER_ERROR_CODES } from './types';
import type { GameState, Phase } from './types';

/**
 * S-6.1.5 handlers (CONTRACT.md 4.7): poison_changed, counter_changed, commander_cast, monarch_set
 * and initiative_set. The shared vectors pin everything reachable through events; this file adds
 * what a vector cannot say: hand-built states (a poison above the threshold, unvalidated numbers,
 * the safe range), the `ending` and the closed phases, and purity of the input.
 */

const poison = (seat: unknown, delta: unknown, source?: unknown) =>
  make(
    'poison_changed',
    source === undefined ? { seat, delta } : { seat, delta, source_seat: source },
  );
const counter = (seat: unknown, name: unknown, delta: unknown) =>
  make('counter_changed', { seat, counter: name, delta });
const cast = (seat: unknown, commander: unknown, delta: unknown) =>
  make('commander_cast', { seat, commander, delta });

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
const withSettings = (state: GameState, change: Record<string, unknown>): GameState => ({
  ...state,
  settings: { ...state.settings, ...change } as GameState['settings'],
});
const canon = (state: GameState) => canonicalJson(state);
const MAX = 9007199254740991;

function deepFreeze<T>(value: T): T {
  if (typeof value === 'object' && value !== null && !Object.isFrozen(value)) {
    Object.freeze(value);
    for (const child of Object.values(value)) deepFreeze(child);
  }
  return value;
}

describe('poison_changed on hand-built states (4.7.1)', () => {
  it('criterion 1: seat 2 at 9 with the default threshold, +1 from seat 4: 10 poison, eliminated by 4', () => {
    const before = withSeat(activeGame(4), 2, { poison: 9 });
    const state = after(before, poison(2, 1, 4));
    expect(seat(state, 2)).toMatchObject({
      poison: 10,
      elimination_reason: 'poison',
      eliminated_by: 4,
      eliminated_at_seq: before.last_seq + 1,
      eliminated_round: 1,
      elimination_cause: null,
      life: 40,
    });
  });

  it('criterion 1: with a threshold of 15 the same seat is eliminated only at 15', () => {
    const before = withSettings(withSeat(activeGame(4), 2, { poison: 9 }), {
      poison_threshold: 15,
    });
    const at14 = after(before, poison(2, 5, 4));
    expect(seat(at14, 2)).toMatchObject({ poison: 14, eliminated_at_seq: null });
    const at15 = after(at14, poison(2, 1, 4));
    expect(seat(at15, 2)).toMatchObject({ poison: 15, elimination_reason: 'poison' });
  });

  it('a negative delta never eliminates, even when poison stays above the threshold (4.5.2 precedence)', () => {
    // A seat can only sit at 12 poison, in the game, after a revive (S-6.6.5): built by hand.
    const before = withSeat(activeGame(4), 2, { poison: 12 });
    const state = after(before, poison(2, -1, 3));
    expect(seat(state, 2)).toMatchObject({ poison: 11, eliminated_at_seq: null });
    expect(state.no_winner).toBe(false);
  });

  it('a positive delta on a seat already above the threshold eliminates: this event raised it', () => {
    const before = withSeat(activeGame(4), 2, { poison: 12 });
    const state = after(before, poison(2, 1));
    expect(seat(state, 2)).toMatchObject({
      poison: 13,
      elimination_reason: 'poison',
      eliminated_by: null,
    });
  });

  it('an integer threshold outside 1 to 99 is used as given', () => {
    const zero = after(withSettings(activeGame(4), { poison_threshold: 0 }), poison(2, 1));
    expect(seat(zero, 2).elimination_reason).toBe('poison');
    const big = withSettings(activeGame(4), { poison_threshold: 1000 });
    expect(seat(after(big, poison(2, 999)), 2)).toMatchObject({
      poison: 999,
      eliminated_at_seq: null,
    });
    const negative = withSettings(activeGame(4), { poison_threshold: -5 });
    expect(seat(after(negative, poison(2, 1)), 2).elimination_reason).toBe('poison');
  });

  it.each([
    ['a string threshold', (s: GameState) => withSettings(s, { poison_threshold: '10' })],
    ['a null threshold', (s: GameState) => withSettings(s, { poison_threshold: null })],
    ['a fractional threshold', (s: GameState) => withSettings(s, { poison_threshold: 1.5 })],
    ['a boolean threshold', (s: GameState) => withSettings(s, { poison_threshold: true })],
    ['a string poison', (s: GameState) => withSeat(s, 2, { poison: '3' })],
    ['a null poison', (s: GameState) => withSeat(s, 2, { poison: null })],
    ['a fractional poison', (s: GameState) => withSeat(s, 2, { poison: 0.5 })],
    ['a boolean poison', (s: GameState) => withSeat(s, 2, { poison: false })],
    ['a negative poison', (s: GameState) => withSeat(s, 2, { poison: -4 })],
  ])('%s is invalid_amount and never throws', (_name, build) => {
    const state = build(activeGame(4));
    for (const delta of [1, -1]) expect(codeOf(state, poison(2, delta))).toBe('invalid_amount');
  });

  it('a new poison above the safe range is invalid_amount', () => {
    const before = withSettings(withSeat(activeGame(4), 2, { poison: MAX }), {
      poison_threshold: MAX,
    });
    expect(codeOf(before, poison(2, 1))).toBe('invalid_amount');
    expect(codeOf(before, poison(2, -1))).toBe('ok');
    const edge = withSettings(withSeat(activeGame(4), 2, { poison: MAX - 1 }), {
      poison_threshold: MAX,
    });
    expect(seat(after(edge, poison(2, 1)), 2)).toMatchObject({
      poison: MAX,
      elimination_reason: 'poison',
    });
  });

  it('works in the ending phase and is invalid_phase in every other phase', () => {
    const base = activeGame(4);
    expect(codeOf(inPhase(base, 'ending'), poison(2, 1))).toBe('ok');
    for (const phase of PHASES.filter((p) => p !== 'active' && p !== 'ending')) {
      expect(codeOf(inPhase(base, phase), poison(2, 1))).toBe('invalid_phase');
    }
  });

  it('is not an elimination of a seat by life or commander damage: only poison is the reason', () => {
    const before = withSeat(activeGame(4), 2, { life: -3 });
    const state = after(before, poison(2, 10, 1));
    expect(seat(state, 2).elimination_reason).toBe('poison');
  });

  it('does not mutate the state or the event', () => {
    const before = deepFreeze(withSeat(activeGame(4), 2, { poison: 9 }));
    const event = deepFreeze(poison(2, 1, 4));
    const r = apply(before, event);
    expect(r.ok).toBe(true);
    expect(seat(before, 2).poison).toBe(9);
  });

  it('the optimistic event (no seq) records last_seq + 1 as eliminated_at_seq', () => {
    const before = withSeat(activeGame(4), 2, { poison: 9 });
    const r = apply(before, poison(2, 1));
    expect(r.ok && seat(r.state, 2).eliminated_at_seq).toBe(before.last_seq + 1);
    const withSeq = apply(before, make('poison_changed', { seat: 2, delta: 1 }, { seq: 6 }));
    expect(
      withSeq.ok && [withSeq.state.last_seq, seat(withSeq.state, 2).eliminated_at_seq],
    ).toEqual([6, 6]);
  });
});

describe('counter_changed on hand-built states (4.7.2)', () => {
  it('the counter is exactly energy or experience', () => {
    const state = activeGame(4);
    for (const bad of [
      'poison',
      'Energy',
      'EXPERIENCE',
      '',
      ' energy',
      'life',
      null,
      1,
      true,
      [],
    ]) {
      expect(codeOf(state, counter(2, bad, 1))).toBe('invalid_payload');
    }
    expect(codeOf(state, make('counter_changed', { seat: 2, delta: 1 }))).toBe('invalid_payload');
  });

  it.each(['energy', 'experience'] as const)(
    '%s changes only that counter of that seat',
    (name) => {
      const state = after(activeGame(4), counter(3, name, 4));
      const other = name === 'energy' ? 'experience' : 'energy';
      expect(seat(state, 3)[name]).toBe(4);
      expect(seat(state, 3)[other]).toBe(0);
      expect(seat(state, 3).poison).toBe(0);
      expect(seat(state, 2)[name]).toBe(0);
    },
  );

  it.each(['energy', 'experience'] as const)('%s at the safe range limit', (name) => {
    const before = withSeat(activeGame(4), 2, { [name]: MAX });
    expect(codeOf(before, counter(2, name, 1))).toBe('invalid_amount');
    expect(seat(after(before, counter(2, name, -1)), 2)[name]).toBe(MAX - 1);
    const edge = withSeat(activeGame(4), 2, { [name]: MAX - 999 });
    expect(seat(after(edge, counter(2, name, 999)), 2)[name]).toBe(MAX);
  });

  it.each([
    ['a string', '3'],
    ['a fraction', 2.5],
    ['null', null],
    ['a boolean', true],
    ['a negative', -4],
  ])('a counter value that is %s is invalid_amount and never throws', (_n, bad) => {
    const state = withSeat(activeGame(4), 2, { energy: bad });
    expect(codeOf(state, counter(2, 'energy', 1))).toBe('invalid_amount');
    expect(codeOf(state, counter(2, 'energy', -1))).toBe('invalid_amount');
  });

  it('never eliminates, whatever the value, and never touches the table', () => {
    const state = run(activeGame(4), counter(1, 'energy', 999), counter(1, 'experience', 999));
    expect(state.seats.every((s) => s.eliminated_at_seq === null)).toBe(true);
    expect([state.active_seat, state.turn, state.round, state.order]).toEqual([
      1,
      1,
      1,
      [1, 2, 3, 4],
    ]);
  });

  it('works in the ending phase and is invalid_phase elsewhere', () => {
    const base = activeGame(4);
    expect(codeOf(inPhase(base, 'ending'), counter(2, 'energy', 1))).toBe('ok');
    for (const phase of PHASES.filter((p) => p !== 'active' && p !== 'ending')) {
      expect(codeOf(inPhase(base, phase), counter(2, 'energy', 1))).toBe('invalid_phase');
    }
  });
});

describe('commander_cast on hand-built states (4.7.3)', () => {
  const partners = (state: GameState, n: number) =>
    withSeat(state, n, {
      commanders: [
        { card_id: null, name: 'A' },
        { card_id: null, name: 'B' },
      ],
      commander_cast: [0, 0],
    });

  it('criterion 2: B twice is 2 (tax 4), A stays 0, and the tax is never stored', () => {
    const state = after(partners(activeGame(4), 2), cast(2, 1, 1));
    const twice = after(state, cast(2, 1, 1));
    expect(seat(twice, 2).commander_cast).toEqual([0, 2]);
    expect(2 * seat(twice, 2).commander_cast[1]!).toBe(4);
    expect(Object.keys(seat(twice, 2))).not.toContain('commander_tax');
    expect(JSON.stringify(twice)).not.toContain('tax');
  });

  it('only +1 and -1 are allowed', () => {
    const state = run(activeGame(4), cast(2, 0, 1), cast(2, 0, 1), cast(2, 0, 1));
    for (const delta of [0, 2, -2, 3, 999, -999, 1000]) {
      expect(codeOf(state, cast(2, 0, delta))).toBe('invalid_amount');
    }
    expect(codeOf(state, cast(2, 0, -1))).toBe('ok');
  });

  it('a count cannot go below 0, per commander', () => {
    const state = after(partners(activeGame(4), 2), cast(2, 0, 1));
    expect(codeOf(state, cast(2, 1, -1))).toBe('invalid_amount');
    expect(codeOf(state, cast(2, 0, -1))).toBe('ok');
  });

  it('unknown_commander is checked against the seat itself, after the delta', () => {
    const state = partners(activeGame(4), 2);
    expect(codeOf(state, cast(2, 2, 1))).toBe('unknown_commander');
    expect(codeOf(state, cast(3, 1, 1))).toBe('unknown_commander');
    expect(codeOf(state, cast(2, -1, 1))).toBe('unknown_commander');
    expect(codeOf(state, cast(2, 5, 2))).toBe('invalid_amount');
    expect(codeOf(state, cast(3, 1, -1))).toBe('unknown_commander');
  });

  it.each([
    ['a string', ['0', 0]],
    ['a fraction', [0.5, 0]],
    ['null', [null, 0]],
    ['a negative', [-5, 0]],
    ['missing (a shorter array)', [] as unknown[]],
  ])('a cast count that is %s is invalid_amount and never throws', (_n, counts) => {
    const state = withSeat(partners(activeGame(4), 2), 2, { commander_cast: counts });
    expect(codeOf(state, cast(2, 0, 1))).toBe('invalid_amount');
    expect(codeOf(state, cast(2, 0, -1))).toBe('invalid_amount');
  });

  it('commander_cast that is not an array is invalid_amount and never throws', () => {
    const state = withSeat(activeGame(4), 2, { commander_cast: null });
    expect(codeOf(state, cast(2, 0, 1))).toBe('invalid_amount');
  });

  it('a count at the safe range limit', () => {
    const at = withSeat(activeGame(4), 2, { commander_cast: [MAX] });
    expect(codeOf(at, cast(2, 0, 1))).toBe('invalid_amount');
    expect(seat(after(at, cast(2, 0, -1)), 2).commander_cast).toEqual([MAX - 1]);
  });

  it('works in the ending phase and is invalid_phase elsewhere', () => {
    const base = activeGame(4);
    expect(codeOf(inPhase(base, 'ending'), cast(2, 0, 1))).toBe('ok');
    for (const phase of PHASES.filter((p) => p !== 'active' && p !== 'ending')) {
      expect(codeOf(inPhase(base, phase), cast(2, 0, 1))).toBe('invalid_phase');
    }
  });

  it('does not mutate the cast array of the input state', () => {
    const before = deepFreeze(activeGame(4));
    const r = apply(before, cast(2, 0, 1));
    expect(r.ok).toBe(true);
    expect(seat(before, 2).commander_cast).toEqual([0]);
  });
});

describe('monarch_set and initiative_set on hand-built states (4.7.4, 4.7.5)', () => {
  const sets = [
    ['monarch_set', 'monarch', 'initiative'],
    ['initiative_set', 'initiative', 'monarch'],
  ] as const;

  it.each(sets)('%s: only one seat holds it, the other key is untouched', (type, key, other) => {
    const base: GameState = { ...activeGame(4), [other]: 4 };
    const state = run(base, make(type, { seat: 1 }), make(type, { seat: 3 }));
    expect([state[key], state[other]]).toEqual([3, 4]);
  });

  it.each(sets)('%s: the current holder again changes only last_seq', (type, key) => {
    const state = run(activeGame(4), make(type, { seat: 2 }));
    const again = run(state, make(type, { seat: 2 }));
    expect(canon(again)).toBe(canon({ ...state, last_seq: state.last_seq + 1 }));
    expect(again[key]).toBe(2);
  });

  it.each(sets)('%s: null cannot clear it (the registry payload has only a seat)', (type) => {
    const state = run(activeGame(4), make(type, { seat: 2 }));
    expect(codeOf(state, make(type, { seat: null }))).toBe('invalid_payload');
  });

  it.each(sets)('%s: ending is allowed, other phases are invalid_phase', (type) => {
    const base = activeGame(4);
    expect(codeOf(inPhase(base, 'ending'), make(type, { seat: 2 }))).toBe('ok');
    for (const phase of PHASES.filter((p) => p !== 'active' && p !== 'ending')) {
      expect(codeOf(inPhase(base, phase), make(type, { seat: 2 }))).toBe('invalid_phase');
    }
  });
});

// ---------------------------------------------------------------------------------------------
// The contract and the code agree on the check order of 4.7.
const contract = readFileSync(new URL('../CONTRACT.md', import.meta.url), 'utf8');
const NL = String.fromCharCode(10);
const DIGITS = String.raw`^\d+\. (.*)$`;

function subsection(heading: string): string {
  const lines = contract.split(NL);
  const start = lines.findIndex((l) => l.startsWith(heading));
  if (start < 0) throw new Error(`no section ${heading}`);
  const end = lines.findIndex((l, i) => i > start && /^#{1,4} /.test(l));
  return lines.slice(start, end < 0 ? undefined : end).join(NL);
}

/** The first reducer error code in each numbered item of a text, in order. */
function numberedCodes(text: string): string[] {
  return [...text.matchAll(new RegExp(DIGITS, 'gm'))].map((m) =>
    [...m[1]!.matchAll(/`([a-z_]+)`/g)]
      .map((t) => t[1]!)
      .find((t) => (REDUCER_ERROR_CODES as readonly string[]).includes(t))!,
  );
}

describe('4.7 lists the checks in the order of 4.5.1 and the code follows it', () => {
  const shared = ['invalid_payload', 'invalid_phase', 'unknown_seat', 'seat_eliminated'];

  it.each([
    ['#### 4.7.1 ', [...shared, 'invalid_amount', 'invalid_amount']],
    [
      '#### 4.7.2 ',
      [...shared.slice(0, 1), ...shared.slice(1), 'invalid_amount', 'invalid_amount'],
    ],
    ['#### 4.7.3 ', [...shared, 'invalid_amount', 'unknown_commander', 'invalid_amount']],
    ['#### 4.7.4 ', shared],
  ])('%s', (heading, codes) => {
    expect(numberedCodes(subsection(heading))).toEqual(codes);
  });

  it('a sender with several mistakes hears about them in that order', () => {
    const live = activeGame(4);
    const gone = run(live, make('player_conceded', { seat: 2 }));
    const lobby = { ...live, phase: 'lobby' as const };
    // poison_changed: shape, phase, unknown seat, eliminated, amount, numbers
    expect(codeOf(lobby, poison(2, '1'))).toBe('invalid_payload');
    expect(codeOf(lobby, poison(7, 0))).toBe('invalid_phase');
    expect(codeOf(gone, poison(7, 0))).toBe('unknown_seat');
    expect(codeOf(gone, poison(2, 0))).toBe('seat_eliminated');
    expect(codeOf(live, poison(2, 0))).toBe('invalid_amount');
    // commander_cast: shape, phase, unknown seat, eliminated, delta, commander, count
    expect(codeOf(lobby, cast(2, 0, 'x'))).toBe('invalid_payload');
    expect(codeOf(gone, cast(2, 9, 2))).toBe('seat_eliminated');
    expect(codeOf(live, cast(2, 9, 2))).toBe('invalid_amount');
    expect(codeOf(live, cast(2, 9, 1))).toBe('unknown_commander');
    expect(codeOf(live, cast(2, 0, -1))).toBe('invalid_amount');
  });

  it('4.5.2 amends its "changes nothing else" list: monarch and initiative change only by the hand-off', () => {
    const text = subsection('#### 4.5.2 ');
    const line = text.split(NL).find((l) => l.startsWith('The routine changes nothing else'))!;
    expect(line).not.toMatch(/win_condition`, `monarch`/);
    expect(line).toContain('`monarch` and `initiative` change only by step 4');
  });
});
