import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { canonicalJson } from './canonical-json';
import { eliminate } from './elimination';
import type { Elimination } from './elimination';
import { codeOf, joined, lobbyState, make, moved, run, started } from './lobby.test.helpers';
import { apply } from './reducer';
import { ELIMINATION_REASONS, REDUCER_ERROR_CODES } from './types';
import type { EventEnvelope, GameSettings, GameState, Phase, Seat } from './types';

/**
 * Acceptance tests of S-6.1.5 (poison, player counters, commander cast, monarch and initiative, and
 * the hand-off of the two crowns), written from the four acceptance criteria of the story (issue
 * #209) and from CONTRACT.md 3.1 to 3.3, 4.1, 4.2, 4.4.7, 4.4.8, 4.4.10, 4.5.1, 4.5.2, 4.6, 4.7, 5,
 * 6 and 8, not from the handlers. The shared vectors are the primary proof for everything reachable
 * through events; this file adds the criteria with their own numbers, built through real events
 * from `game_created`, the check lists and example tables parsed from the contract (so the document
 * and the code cannot drift), the phases no log reaches yet, purity, the "only what this event
 * raised" rule on hand-built states, and an oracle written from the contract's words over seeded
 * generated cases.
 *
 * The Python twin is `apps/api/app/games/tests/test_s615_acceptance.py`: same cases, same numbers.
 */

const contract = readFileSync(new URL('../CONTRACT.md', import.meta.url), 'utf8');
const MAX_SAFE = 9007199254740991;

type Payload = Record<string, unknown>;
interface PSeat {
  seat: number;
  life: unknown;
  poison: unknown;
  energy: unknown;
  experience: unknown;
  commanders: unknown[];
  commander_cast: unknown[];
  elimination_reason: string | null;
  eliminated_by: number | null;
  eliminated_at_seq: number | null;
  eliminated_round: number | null;
  elimination_cause: string | null;
}
interface Plain {
  phase: string;
  round: number;
  turn: number;
  last_seq: number;
  no_winner: boolean;
  monarch: number | null;
  initiative: number | null;
  active_seat: number | null;
  first_seat: number | null;
  order: number[];
  seats: PSeat[];
  commander_damage: Record<string, Record<string, number>>;
  settings: Record<string, unknown>;
}

const ev = (type: string, payload: Payload = {}): EventEnvelope => make(type, payload);
/** `poison_changed {seat, delta, source_seat?}`. */
const pc = (seat: unknown, delta: unknown, source?: unknown): EventEnvelope =>
  make(
    'poison_changed',
    source === undefined ? { seat, delta } : { seat, delta, source_seat: source },
  );
/** `counter_changed {seat, counter, delta}`. */
const cc = (seat: unknown, counter: unknown, delta: unknown): EventEnvelope =>
  make('counter_changed', { seat, counter, delta });
/** `commander_cast {seat, commander, delta}`. */
const cast = (seat: unknown, commander: unknown, delta: unknown): EventEnvelope =>
  make('commander_cast', { seat, commander, delta });
const monarchSet = (seat: unknown): EventEnvelope => make('monarch_set', { seat });
const initiativeSet = (seat: unknown): EventEnvelope => make('initiative_set', { seat });
const lc = (seat: number, delta: number, source?: number): EventEnvelope =>
  make(
    'life_changed',
    source === undefined ? { seat, delta } : { seat, delta, source_seat: source },
  );
const concede = (seat: number): EventEnvelope => make('player_conceded', { seat });
const declare = (seat: number, cause?: string): EventEnvelope =>
  make('player_eliminated', cause === undefined ? { seat } : { seat, cause });
const cd = (source: number, commander: number, target: number, amount: number): EventEnvelope =>
  make('commander_damage', { source_seat: source, commander, target_seat: target, amount });
const turn = (): EventEnvelope => make('turn_passed');
const holderSet = (key: 'monarch' | 'initiative') =>
  key === 'monarch' ? monarchSet : initiativeSet;

const PARTNER_A = { card_id: null, name: 'Partner A' };
const PARTNER_B = { card_id: '018f0000-0000-7000-8000-0000000000e2', name: 'Partner B' };
const PARTNERS = [PARTNER_A, PARTNER_B];
const SOLO = [{ card_id: null, name: '' }];

interface Opts {
  count?: number;
  poisonThreshold?: number;
  life?: number;
  /** Seats given a partner pair with `commanders_set`. */
  partners?: number[];
  /** Seats given a partner pair with `deck_assigned`. */
  decks?: number[];
  /** The first seat of `game_started`. */
  first?: number;
}

/** The lobby of a game as a real log: `game_created`, the seats, the commanders, no start. */
function lobbyLog(o: Opts = {}): EventEnvelope[] {
  const settings = {
    starting_life: o.life ?? 40,
    commander_damage_threshold: 21,
    poison_threshold: o.poisonThreshold ?? 10,
    trust_mode: 'casual',
  };
  return [
    ev('game_created', { settings }),
    ...Array.from({ length: o.count ?? 4 }, (_, i) => joined(i + 1)),
    ...(o.partners ?? []).map((seat) => ev('commanders_set', { seat, commanders: PARTNERS })),
    ...(o.decks ?? []).map((seat) =>
      ev('deck_assigned', { seat, deck_version_id: `deck-${seat}`, commanders: PARTNERS }),
    ),
  ];
}
const lobbyOf = (o: Opts = {}): GameState => run(lobbyState(), ...lobbyLog(o));
/** A started game built from `game_created`, seat 1 first unless `first` says otherwise. */
const game = (o: Opts = {}): GameState => run(lobbyOf(o), started(o.first ?? 1));
/** The same game from the placeholder settings of `initialState` (they are not validated). */
const rawGame = (settings: Record<string, unknown>, count = 4): GameState =>
  run(
    lobbyState(settings as Partial<GameSettings>),
    ...Array.from({ length: count }, (_, i) => joined(i + 1)),
    started(1),
  );

const seatOf = (state: GameState, id: number): Seat => state.seats.find((s) => s.seat === id)!;
const inGame = (state: GameState): number[] =>
  state.seats.filter((s) => s.eliminated_at_seq === null).map((s) => s.seat);
const inPhase = (state: GameState, phase: Phase): GameState => ({
  ...state,
  phase,
  resume_phase: phase === 'abandoned' ? 'active' : null,
});
const withSeat = (state: GameState, id: number, changes: Record<string, unknown>): GameState => ({
  ...state,
  seats: state.seats.map((s) => (s.seat === id ? ({ ...s, ...changes } as Seat) : s)),
});
const withSettings = (state: GameState, changes: Record<string, unknown>): GameState => ({
  ...state,
  settings: { ...state.settings, ...changes } as GameState['settings'],
});
/** A seat eliminated by life loss at `atSeq`, built by hand. */
const outAt = (state: GameState, id: number, atSeq: number, round = 1): GameState =>
  withSeat(state, id, {
    life: 0,
    elimination_reason: 'life',
    eliminated_by: null,
    eliminated_at_seq: atSeq,
    eliminated_round: round,
    elimination_cause: null,
  });
const canon = (state: GameState): string => canonicalJson(state);
const plain = (state: GameState): Plain => JSON.parse(canon(state)) as Plain;
/** The state without the keys listed, canonical: compares "everything else". */
const without = (state: GameState, ...keys: string[]): string => {
  const copy = JSON.parse(canon(state)) as Record<string, unknown>;
  for (const k of keys) delete copy[k];
  return JSON.stringify(copy);
};
const MONARCH_KEYS = ['monarch', 'initiative'] as const;

/** Recursively freeze, so that any mutation by a reducer throws (modules are strict). */
function deepFreeze<T>(value: T): T {
  if (typeof value === 'object' && value !== null && !Object.isFrozen(value)) {
    Object.freeze(value);
    for (const v of Object.values(value)) deepFreeze(v);
  }
  return value;
}

// --- parsing the contract ---------------------------------------------------------------------

function section(headingPrefix: string): string {
  const lines = contract.split('\n');
  const start = lines.findIndex((l) => l.startsWith(headingPrefix));
  if (start < 0) throw new Error(`no section ${headingPrefix}`);
  let end = lines.length;
  for (let i = start + 1; i < lines.length; i++) {
    if (/^#{1,4} /.test(lines[i]!)) {
      end = i;
      break;
    }
  }
  return lines.slice(start, end).join('\n');
}

/** The first reducer error code in each numbered item of a text, in order. */
function numberedCodes(text: string): string[] {
  const out: string[] = [];
  for (const m of text.matchAll(/^\d+\. (.*)$/gm)) {
    const code = [...m[1]!.matchAll(/`([a-z_]+)`/g)]
      .map((t) => t[1]!)
      .find((t) => (REDUCER_ERROR_CODES as readonly string[]).includes(t));
    if (code !== undefined) out.push(code);
  }
  return out;
}

/** The rows of the tables of `text` whose first cell is a number: cells trimmed. */
function tableRows(text: string): string[][] {
  return text
    .split('\n')
    .filter((l) => /^\| \d+ +\|/.test(l))
    .map((l) =>
      l
        .split('|')
        .slice(1, -1)
        .map((c) => c.trim()),
    );
}

const S471 = section('#### 4.7.1 ');
const S472 = section('#### 4.7.2 ');
const S473 = section('#### 4.7.3 ');
const S474 = section('#### 4.7.4 ');
const S475 = section('#### 4.7.5 ');
const S476 = section('#### 4.7.6 ');
const CHECKS_471 = numberedCodes(S471);
const CHECKS_472 = numberedCodes(S472);
const CHECKS_473 = numberedCodes(S473.slice(0, S473.indexOf('Why check 5 comes before check 6')));
const CHECKS_474 = numberedCodes(S474);
const ROWS_471 = tableRows(S471);
const ROWS_472 = tableRows(S472);
const ROWS_473 = tableRows(S473);
const ROWS_476 = tableRows(S476);

// ---------------------------------------------------------------------------------------------
describe('the contract text this file relies on', () => {
  it('4.7.1 lists six checks: shape, phase, unknown_seat, seat_eliminated, amount, numbers', () => {
    expect(CHECKS_471).toEqual([
      'invalid_payload',
      'invalid_phase',
      'unknown_seat',
      'seat_eliminated',
      'invalid_amount',
      'invalid_amount',
    ]);
  });

  it('4.7.2 lists the same six checks', () => {
    expect(CHECKS_472).toEqual(CHECKS_471);
  });

  it('4.7.3 lists seven checks: the shared four, then amount, commander, numbers', () => {
    expect(CHECKS_473).toEqual([
      'invalid_payload',
      'invalid_phase',
      'unknown_seat',
      'seat_eliminated',
      'invalid_amount',
      'unknown_commander',
      'invalid_amount',
    ]);
  });

  it('4.7.4 lists four checks and 4.7.5 says its checks 1 to 4 are the same', () => {
    expect(CHECKS_474).toEqual([
      'invalid_payload',
      'invalid_phase',
      'unknown_seat',
      'seat_eliminated',
    ]);
    expect(S475).toContain('the checks 1 to 4 are the same');
  });

  it('every list of 4.7 starts with the order of 4.5.1: shape, phase, unknown_seat, seat_eliminated', () => {
    const shared = numberedCodes(section('#### 4.5.1 ')).slice(0, 4);
    expect(shared).toEqual(['invalid_payload', 'invalid_phase', 'unknown_seat', 'seat_eliminated']);
    for (const list of [CHECKS_471, CHECKS_472, CHECKS_473, CHECKS_474]) {
      expect(list.slice(0, 4)).toEqual(shared);
    }
  });

  it('4.7 gives the examples these tests are built from: 16, 9, 10 and 13 rows', () => {
    expect(ROWS_471.map((r) => Number(r[0]))).toEqual(Array.from({ length: 16 }, (_, i) => i + 1));
    expect(ROWS_472.map((r) => Number(r[0]))).toEqual(Array.from({ length: 9 }, (_, i) => i + 1));
    expect(ROWS_473.map((r) => Number(r[0]))).toEqual(Array.from({ length: 10 }, (_, i) => i + 1));
    expect(ROWS_476.map((r) => Number(r[0]))).toEqual(Array.from({ length: 13 }, (_, i) => i + 1));
  });

  it('the registry rows of the five types give the payload keys of the story', () => {
    const row = (type: string) =>
      contract.split('\n').find((l) => l.startsWith(`| \`${type}\``) && l.includes('S-6.1.5'))!;
    expect(row('poison_changed')).toContain('`seat`, `delta`, `source_seat?`');
    expect(row('counter_changed')).toContain(
      '`seat`, `counter` (`energy` or `experience`), `delta`',
    );
    expect(row('commander_cast')).toContain('`seat`, `commander` (index), `delta`');
    expect(row('monarch_set')).toContain('| `seat`');
    expect(row('initiative_set')).toContain('| `seat`');
  });

  it('4.5.2 gives the precedence commander_damage, poison, life, and 4.7.1 uses poison alone', () => {
    const bullet = section('#### 4.5.2 ')
      .split('\n')
      .find((l) => l.startsWith('- **Reason precedence'))!;
    const tail = bullet.slice(bullet.indexOf('in the order') + 'in the order'.length);
    const order = [...tail.slice(0, tail.indexOf('.')).matchAll(/`([a-z_]+)`/g)].map((m) => m[1]!);
    expect(order).toEqual(['commander_damage', 'poison', 'life']);
    for (const r of order) expect(ELIMINATION_REASONS as readonly string[]).toContain(r);
    expect(S471).toContain('the entry `(seat, poison, by, null)`');
  });

  it('3.2 gives poison_threshold the range 1 to 99 and the default 10', () => {
    const row = section('### 3.2 ')
      .split('\n')
      .find((l) => l.startsWith('| `poison_threshold`'))!
      .split('|')
      .map((c) => c.trim());
    expect([row[3], row[4]]).toEqual(['1 to 99', '10']);
    expect(lobbyState().settings.poison_threshold).toBe(10);
  });

  it('4.5.2 has a step 4 for the hand-off with the three outcomes: null, the active seat, the next seat', () => {
    const text = section('#### 4.5.2 ');
    const start = text.indexOf('4. **Monarch and initiative hand-off**');
    expect(start).toBeGreaterThan(0);
    const step = text.slice(start, text.indexOf('The routine changes nothing else'));
    const items = [...step.matchAll(/^\s+\d\. (.*)$/gm)].map((m) => m[1]!);
    expect(items).toHaveLength(3);
    expect(items[0]).toContain('`null`');
    expect(items[1]).toContain('the `active_seat`');
    expect(items[2]).toContain('**next seat**');
  });
});

// ---------------------------------------------------------------------------------------------
describe('criterion 1: poison, the threshold, and amounts below 0', () => {
  /** Seat 2 raised to 9 poison by an event, as the criterion has it. */
  const nine = (o: Opts = {}): GameState => run(game(o), pc(2, 9));

  it('seat 2 is at 9 poison after an event of +9, in the game', () => {
    const state = nine();
    expect(seatOf(state, 2)).toMatchObject({ poison: 9, eliminated_at_seq: null });
  });

  it('poison_changed {seat 2, delta 1, source_seat 4} gives 10 poison and eliminates with reason poison, eliminated_by 4', () => {
    const before = nine();
    const after = run(before, pc(2, 1, 4));
    expect(seatOf(after, 2)).toMatchObject({
      poison: 10,
      elimination_reason: 'poison',
      eliminated_by: 4,
      eliminated_at_seq: before.last_seq + 1,
      eliminated_round: 1,
      elimination_cause: null,
      life: 40,
    });
    expect(after.last_seq).toBe(before.last_seq + 1);
  });

  it('the other seats, the active seat, the turn and the round are untouched, and nobody has won', () => {
    const before = nine();
    const after = run(before, pc(2, 1, 4));
    for (const id of [1, 3, 4]) expect(seatOf(after, id)).toEqual(seatOf(before, id));
    expect(inGame(after)).toEqual([1, 3, 4]);
    expect([after.active_seat, after.turn, after.round]).toEqual([1, 1, 1]);
    expect([after.no_winner, after.winner_seat, after.win_condition, after.phase]).toEqual([
      false,
      null,
      null,
      'active',
    ]);
  });

  it('eliminated_at_seq is the seq the event carries when the server assigned it', () => {
    const before = nine();
    const seq = before.last_seq + 1;
    const after = run(
      before,
      make('poison_changed', { seat: 2, delta: 1, source_seat: 4 }, { seq }),
    );
    expect(seatOf(after, 2).eliminated_at_seq).toBe(seq);
  });

  it('the same numbers from the default settings of initialState, without a game_created', () => {
    const after = run(rawGame({}), pc(2, 9), pc(2, 1, 4));
    expect(seatOf(after, 2)).toMatchObject({
      poison: 10,
      elimination_reason: 'poison',
      eliminated_by: 4,
    });
  });

  it('with the default threshold 9 stays in the game and 10 eliminates, one point at a time', () => {
    let state = game();
    expect(state.settings.poison_threshold).toBe(10);
    for (let n = 1; n <= 9; n++) {
      state = run(state, pc(2, 1));
      expect(seatOf(state, 2)).toMatchObject({ poison: n, eliminated_at_seq: null });
    }
    state = run(state, pc(2, 1, 4));
    expect(seatOf(state, 2)).toMatchObject({
      poison: 10,
      elimination_reason: 'poison',
      eliminated_by: 4,
    });
  });

  describe('poison_threshold 15: 10 to 14 do not eliminate, 15 does', () => {
    const viaGameCreated = () => game({ poisonThreshold: 15 });
    const viaSettingsChanged = () =>
      run(lobbyOf(), ev('settings_changed', { poison_threshold: 15 }), started(1));
    const viaInitialState = () => rawGame({ poison_threshold: 15 });

    it.each([
      ['game_created', viaGameCreated],
      ['settings_changed', viaSettingsChanged],
      ['the settings of initialState', viaInitialState],
    ])('set through %s', (_how, build) => {
      let state = build();
      expect(state.settings.poison_threshold).toBe(15);
      state = run(state, pc(2, 9));
      for (let n = 10; n <= 14; n++) {
        state = run(state, pc(2, 1, 4));
        expect(seatOf(state, 2), `at ${n}`).toMatchObject({ poison: n, eliminated_at_seq: null });
      }
      const after = run(state, pc(2, 1, 4));
      expect(seatOf(after, 2)).toMatchObject({
        poison: 15,
        elimination_reason: 'poison',
        eliminated_by: 4,
        eliminated_at_seq: after.last_seq,
      });
    });

    it('10 poison in one event stays in the game and 15 in one event eliminates', () => {
      expect(seatOf(run(viaGameCreated(), pc(2, 10)), 2).eliminated_at_seq).toBeNull();
      expect(seatOf(run(viaGameCreated(), pc(2, 15)), 2).elimination_reason).toBe('poison');
    });
  });

  it('threshold 1: the first counter eliminates; threshold 99: 98 stays, 99 eliminates', () => {
    expect(seatOf(run(game({ poisonThreshold: 1 }), pc(2, 1, 3)), 2)).toMatchObject({
      poison: 1,
      elimination_reason: 'poison',
      eliminated_by: 3,
    });
    const held = run(game({ poisonThreshold: 99 }), pc(2, 98));
    expect(seatOf(held, 2).eliminated_at_seq).toBeNull();
    expect(seatOf(run(held, pc(2, 1)), 2)).toMatchObject({
      poison: 99,
      elimination_reason: 'poison',
    });
  });

  const settingsWith = (value: unknown) => ({
    starting_life: 40,
    commander_damage_threshold: 21,
    poison_threshold: value,
    trust_mode: 'casual',
  });

  it.each([1, 10, 15, 99])(
    'game_created and settings_changed accept the poison_threshold %i',
    (value) => {
      expect(codeOf(lobbyState(), ev('game_created', { settings: settingsWith(value) }))).toBe(
        'ok',
      );
      expect(codeOf(lobbyState(), ev('settings_changed', { poison_threshold: value }))).toBe('ok');
    },
  );

  it.each([0, 100, -1, 101, 1000, MAX_SAFE, '10', true, null])(
    'the poison_threshold %j is invalid_setting in game_created and in settings_changed',
    (value) => {
      expect(codeOf(lobbyState(), ev('game_created', { settings: settingsWith(value) }))).toBe(
        'invalid_setting',
      );
      expect(codeOf(lobbyState(), ev('settings_changed', { poison_threshold: value }))).toBe(
        'invalid_setting',
      );
    },
  );

  it('a rejected threshold leaves the settings as they were', () => {
    const lobby = lobbyOf({ poisonThreshold: 15 });
    expect(codeOf(lobby, ev('settings_changed', { poison_threshold: 100 }))).toBe(
      'invalid_setting',
    );
    expect(codeOf(lobby, ev('settings_changed', { poison_threshold: 0 }))).toBe('invalid_setting');
    expect(lobby.settings.poison_threshold).toBe(15);
  });

  it('the threshold cannot change once the game started (settings_changed is invalid_phase)', () => {
    expect(codeOf(game(), ev('settings_changed', { poison_threshold: 15 }))).toBe('invalid_phase');
  });

  it('an integer poison_threshold outside 1 to 99 in an unvalidated state is used as given (4.7)', () => {
    const zero = run(rawGame({ poison_threshold: 0 }), pc(2, 1));
    expect(seatOf(zero, 2)).toMatchObject({ poison: 1, elimination_reason: 'poison' });
    const huge = run(rawGame({ poison_threshold: 5000 }), pc(2, 999), pc(2, 999));
    expect(seatOf(huge, 2)).toMatchObject({ poison: 1998, eliminated_at_seq: null });
  });

  // Any change that would take poison, energy or experience below 0 is invalid_amount.
  const FIELDS = ['poison', 'energy', 'experience'] as const;
  const bump = (field: (typeof FIELDS)[number], seat: number, delta: number): EventEnvelope =>
    field === 'poison' ? pc(seat, delta) : cc(seat, field, delta);

  describe.each(FIELDS)('%s never goes below 0', (field) => {
    it('at exactly 0, a change of -1 is invalid_amount and the state is unchanged', () => {
      const state = game();
      expect(codeOf(state, bump(field, 2, -1))).toBe('invalid_amount');
      expect(codeOf(state, bump(field, 2, -999))).toBe('invalid_amount');
    });

    it.each([
      [1, -2],
      [2, -3],
      [5, -6],
      [9, -10],
      [3, -999],
    ])('from %i, a change of %i is invalid_amount and the state is unchanged', (start, delta) => {
      const state = run(game(), bump(field, 2, start));
      const snapshot = canon(state);
      expect(codeOf(state, bump(field, 2, delta))).toBe('invalid_amount');
      expect(canon(state)).toBe(snapshot);
      expect(seatOf(state, 2)[field]).toBe(start);
    });

    it.each([
      [1, -1],
      [2, -2],
      [5, -5],
      [9, -9],
    ])('from %i, a change of %i is allowed and gives exactly 0', (start, delta) => {
      const state = run(game(), bump(field, 2, start), bump(field, 2, delta));
      expect(seatOf(state, 2)[field]).toBe(0);
      expect(seatOf(state, 2).eliminated_at_seq).toBeNull();
    });

    it('a rejected event leaves last_seq and every other seat alone', () => {
      const state = run(game(), bump(field, 3, 4));
      const result = apply(state, bump(field, 2, -1));
      expect(result.ok).toBe(false);
      expect(state.last_seq).toBe(game().last_seq + 1);
    });
  });

  it.each([0, 1000, -1000, MAX_SAFE, -MAX_SAFE])(
    'a poison delta of %i is invalid_amount',
    (delta) => {
      expect(codeOf(game(), pc(2, delta))).toBe('invalid_amount');
    },
  );

  it.each([1, 2, 998, 999])('a poison delta of %i is accepted', (delta) => {
    expect(codeOf(game({ poisonThreshold: 99 }), pc(2, delta))).toBe('ok');
  });

  it('a poison delta of 999 and of -999 give the exact totals (threshold 99 is not reached below 99)', () => {
    const state = rawGame({ poison_threshold: 5000 });
    const up = run(state, pc(2, 999));
    expect(seatOf(up, 2).poison).toBe(999);
    expect(seatOf(run(up, pc(2, -999)), 2).poison).toBe(0);
  });

  it.each(['1', true, false, null, [], {}, 1.5, -0.5])(
    'a poison delta of %j is of the wrong type: invalid_payload, not invalid_amount',
    (delta) => {
      expect(codeOf(game(), pc(2, delta))).toBe('invalid_payload');
    },
  );

  it('a poison delta outside the safe range is invalid_payload (6.2 step 5)', () => {
    expect(codeOf(game(), pc(2, MAX_SAFE + 1))).toBe('invalid_payload');
    expect(codeOf(game(), pc(2, -MAX_SAFE - 1))).toBe('invalid_payload');
  });

  it('a counter delta of 0, 1000 and -1000 is invalid_amount; 999 is accepted', () => {
    for (const delta of [0, 1000, -1000]) {
      expect(codeOf(game(), cc(2, 'energy', delta))).toBe('invalid_amount');
    }
    expect(codeOf(game(), cc(2, 'experience', 999))).toBe('ok');
  });

  it('poison changes only the poison and last_seq of a seat below the threshold', () => {
    const before = game({ partners: [2] });
    const after = run(before, pc(2, 3, 4));
    const p = plain(before);
    p.seats.find((s) => s.seat === 2)!.poison = 3;
    p.last_seq += 1;
    expect(canon(after)).toBe(canonicalJson(p));
  });
});

// ---------------------------------------------------------------------------------------------
describe('criterion 2: commander cast count and the derived tax', () => {
  const assignments: Array<[string, Opts]> = [
    ['commanders_set', { partners: [2] }],
    ['deck_assigned', { decks: [2] }],
  ];
  const tax = (state: GameState, id: number, index: number): number =>
    2 * seatOf(state, id).commander_cast[index]!;

  it.each(assignments)(
    'seat 2 has partners A and B from %s, with cast counts [0, 0]',
    (_how, opts) => {
      const state = game(opts);
      expect(seatOf(state, 2).commanders).toEqual(PARTNERS);
      expect(seatOf(state, 2).commander_cast).toEqual([0, 0]);
    },
  );

  it.each(assignments)(
    'seat 2 has partners from %s: casting B twice gives B a count of 2 and a tax of 4, and A stays 0 / 0',
    (_how, opts) => {
      const before = game(opts);
      const once = run(before, cast(2, 1, 1));
      expect(seatOf(once, 2).commander_cast).toEqual([0, 1]);
      expect(tax(once, 2, 1)).toBe(2);
      const twice = run(once, cast(2, 1, 1));
      expect(seatOf(twice, 2).commander_cast).toEqual([0, 2]);
      expect(tax(twice, 2, 1)).toBe(4);
      expect(seatOf(twice, 2).commander_cast[0]).toBe(0);
      expect(tax(twice, 2, 0)).toBe(0);
      expect(twice.last_seq).toBe(before.last_seq + 2);
    },
  );

  it('the tax is derived: no key of the state holds it', () => {
    const state = run(game({ partners: [2] }), cast(2, 1, 1), cast(2, 1, 1));
    expect(canon(state)).not.toMatch(/tax/);
    expect(Object.keys(seatOf(state, 2)).sort()).toEqual(
      [
        'commander_cast',
        'commanders',
        'deck_version_id',
        'eliminated_at_seq',
        'eliminated_by',
        'eliminated_round',
        'elimination_cause',
        'elimination_reason',
        'energy',
        'experience',
        'life',
        'name',
        'player_id',
        'poison',
        'seat',
      ].sort(),
    );
  });

  it('casting A and B in turn counts them separately, and the tax is 2 times each count', () => {
    const state = run(
      game({ partners: [2] }),
      cast(2, 0, 1),
      cast(2, 1, 1),
      cast(2, 1, 1),
      cast(2, 0, 1),
      cast(2, 1, 1),
    );
    expect(seatOf(state, 2).commander_cast).toEqual([2, 3]);
    expect([tax(state, 2, 0), tax(state, 2, 1)]).toEqual([4, 6]);
  });

  it('a delta of -1 is allowed down to a count of 0: [0, 2] gives [0, 1], then [0, 0]', () => {
    const base = run(game({ partners: [2] }), cast(2, 1, 1), cast(2, 1, 1));
    const one = run(base, cast(2, 1, -1));
    expect(seatOf(one, 2).commander_cast).toEqual([0, 1]);
    const zero = run(one, cast(2, 1, -1));
    expect(seatOf(zero, 2).commander_cast).toEqual([0, 0]);
    expect(tax(zero, 2, 1)).toBe(0);
  });

  it('a delta of -1 at a count of 0 is invalid_amount and changes nothing', () => {
    const state = game({ partners: [2] });
    const snapshot = canon(state);
    expect(codeOf(state, cast(2, 1, -1))).toBe('invalid_amount');
    expect(codeOf(state, cast(2, 0, -1))).toBe('invalid_amount');
    expect(canon(state)).toBe(snapshot);
  });

  it('A at 1 going to 0 does not touch B', () => {
    const state = run(game({ partners: [2] }), cast(2, 0, 1), cast(2, 1, 1), cast(2, 0, -1));
    expect(seatOf(state, 2).commander_cast).toEqual([0, 1]);
  });

  it.each([0, 2, -2, 1000, -1000, MAX_SAFE])('a cast delta of %i is invalid_amount', (delta) => {
    expect(codeOf(game({ partners: [2] }), cast(2, 0, delta))).toBe('invalid_amount');
  });

  it.each(['1', true, null, [], {}, 1.5])('a cast delta of %j is invalid_payload', (delta) => {
    expect(codeOf(game({ partners: [2] }), cast(2, 0, delta))).toBe('invalid_payload');
  });

  it('a seat with the unnamed default commander has index 0 only', () => {
    const state = game();
    expect(seatOf(state, 3).commanders).toEqual(SOLO);
    expect(seatOf(run(state, cast(3, 0, 1)), 3).commander_cast).toEqual([1]);
    expect(codeOf(state, cast(3, 1, 1))).toBe('unknown_commander');
  });

  it.each([2, 3, -1, 1000000, MAX_SAFE])(
    'commander %i of a seat with partners is unknown_commander',
    (index) => {
      expect(codeOf(game({ partners: [2] }), cast(2, index, 1))).toBe('unknown_commander');
    },
  );

  it('the index is checked against the seat that casts: a partner seat next to a single-commander seat', () => {
    const state = game({ partners: [2] });
    expect(codeOf(state, cast(2, 1, 1))).toBe('ok');
    expect(codeOf(state, cast(3, 1, 1))).toBe('unknown_commander');
  });

  it('the delta is judged before the commander index, the commander index before the count', () => {
    const state = game();
    expect(codeOf(state, cast(3, 5, 2))).toBe('invalid_amount');
    expect(codeOf(state, cast(3, 1, -1))).toBe('unknown_commander');
  });

  it('seats count their casts independently', () => {
    const state = run(game(), cast(1, 0, 1), cast(4, 0, 1), cast(4, 0, 1));
    expect(state.seats.map((s) => s.commander_cast)).toEqual([[1], [0], [0], [2]]);
  });

  it('a cast changes only the count and last_seq: no tally, poison, life or elimination', () => {
    const before = run(game({ partners: [2] }), cd(1, 0, 2, 5), pc(2, 3));
    const after = run(before, cast(2, 0, 1));
    const p = plain(before);
    p.seats.find((s) => s.seat === 2)!.commander_cast = [1, 0];
    p.last_seq += 1;
    expect(canon(after)).toBe(canonicalJson(p));
  });

  it('commanders replaced in the lobby reset the counts to 0s of the new length', () => {
    const lobby = run(
      lobbyOf({ partners: [2] }),
      ev('commanders_set', { seat: 2, commanders: SOLO }),
    );
    const state = run(lobby, started(1));
    expect(seatOf(state, 2).commander_cast).toEqual([0]);
    expect(codeOf(state, cast(2, 1, 1))).toBe('unknown_commander');
  });
});

// ---------------------------------------------------------------------------------------------
describe('criterion 3: the monarch and the initiative', () => {
  const HOLD = [
    ['monarch', monarchSet],
    ['initiative', initiativeSet],
  ] as const;

  it('nobody holds either at the start of a game', () => {
    const state = game();
    expect([state.monarch, state.initiative]).toEqual([null, null]);
  });

  it('seat 1 is the monarch, then monarch_set {seat 3}: seat 3 is the only monarch and nothing else changed', () => {
    const before = run(game(), monarchSet(1));
    expect(before.monarch).toBe(1);
    const after = run(before, monarchSet(3));
    expect(after.monarch).toBe(3);
    expect(after.last_seq).toBe(before.last_seq + 1);
    expect(without(after, 'monarch', 'last_seq')).toBe(without(before, 'monarch', 'last_seq'));
    expect(after.initiative).toBeNull();
  });

  it('initiative_set works the same way: seat 1 holds it, then seat 3 is the only holder', () => {
    const before = run(game(), initiativeSet(1));
    const after = run(before, initiativeSet(3));
    expect([before.initiative, after.initiative]).toEqual([1, 3]);
    expect(without(after, 'initiative', 'last_seq')).toBe(
      without(before, 'initiative', 'last_seq'),
    );
    expect(after.monarch).toBeNull();
  });

  it.each(HOLD)('%s: one key holds one seat id, so no other seat can be a holder', (key, set) => {
    const state = run(game(), set(1), set(2), set(4), set(3));
    expect(state[key]).toBe(3);
    expect(typeof state[key]).toBe('number');
  });

  // All four combinations of who holds what, and which of the two moves.
  it.each([
    ['monarch 1, initiative 1', [1, 1], 'monarch', 3, [3, 1]],
    ['monarch 1, initiative 1', [1, 1], 'initiative', 3, [1, 3]],
    ['monarch 1, initiative 2', [1, 2], 'monarch', 2, [2, 2]],
    ['monarch 1, initiative 2', [1, 2], 'monarch', 3, [3, 2]],
    ['monarch 1, initiative 2', [1, 2], 'initiative', 1, [1, 1]],
    ['monarch 1, initiative 2', [1, 2], 'initiative', 3, [1, 3]],
  ] as const)('%s: moving the %s to seat %i gives %j', (_label, before, key, to, expected) => {
    const start = run(game(), monarchSet(before[0]), initiativeSet(before[1]));
    expect([start.monarch, start.initiative]).toEqual(before);
    const after = run(start, holderSet(key)(to));
    expect([after.monarch, after.initiative]).toEqual(expected);
  });

  it('one seat can hold both, and the two move independently afterwards', () => {
    const both = run(game(), monarchSet(2), initiativeSet(2));
    expect([both.monarch, both.initiative]).toEqual([2, 2]);
    const monarchMoved = run(both, monarchSet(4));
    expect([monarchMoved.monarch, monarchMoved.initiative]).toEqual([4, 2]);
    const initiativeMoved = run(both, initiativeSet(4));
    expect([initiativeMoved.monarch, initiativeMoved.initiative]).toEqual([2, 4]);
  });

  it.each(HOLD)('naming the current %s holder again changes nothing but last_seq', (key, set) => {
    const before = run(game(), set(2));
    const after = run(before, set(2));
    expect(after[key]).toBe(2);
    expect(after.last_seq).toBe(before.last_seq + 1);
    expect(without(after, 'last_seq')).toBe(without(before, 'last_seq'));
  });

  it.each(HOLD)('%s_set does not move the active seat, the turn or the round', (_key, set) => {
    const before = run(game(), turn(), turn());
    const after = run(before, set(4));
    expect([after.active_seat, after.turn, after.round, after.order]).toEqual([
      before.active_seat,
      before.turn,
      before.round,
      before.order,
    ]);
  });

  it.each(HOLD)(
    'there is no event to clear the %s: seat null and a missing seat are invalid_payload',
    (_key, set) => {
      const state = run(game(), set(2));
      expect(codeOf(state, set(null))).toBe('invalid_payload');
      expect(codeOf(state, ev(`${_key}_set`, {}))).toBe('invalid_payload');
    },
  );

  it.each(HOLD)('%s: a seat that is not an integer from 1 to 8 is invalid_payload', (_key, set) => {
    for (const seat of ['2', true, 0, 9, -1, 1.5, []]) {
      expect(codeOf(game(), set(seat)), JSON.stringify(seat)).toBe('invalid_payload');
    }
  });

  it.each(HOLD)('%s: a seat id that is not at the table is unknown_seat', (_key, set) => {
    expect(codeOf(game({ count: 3 }), set(4))).toBe('unknown_seat');
    expect(codeOf(game(), set(8))).toBe('unknown_seat');
  });

  it.each(HOLD)(
    '%s: an eliminated seat cannot be named, and neither can the one that held it',
    (key, set) => {
      const out = run(game(), concede(2));
      expect(codeOf(out, set(2))).toBe('seat_eliminated');
      const lost = run(game(), set(3), concede(3));
      expect(lost[key]).toBe(1);
      expect(codeOf(lost, set(3))).toBe('seat_eliminated');
    },
  );

  it('the monarch can be named again after its crown moved on through an elimination', () => {
    const state = run(game(), monarchSet(3), concede(3), monarchSet(2));
    expect(state.monarch).toBe(2);
  });

  it('the two keys are unaffected by every other counter event', () => {
    const state = run(game({ partners: [2] }), monarchSet(2), initiativeSet(3));
    const after = run(state, pc(2, 2), cc(2, 'energy', 1), cast(2, 1, 1), lc(2, -5), turn());
    expect([after.monarch, after.initiative]).toEqual([2, 3]);
  });
});

// ---------------------------------------------------------------------------------------------
describe('criterion 4: the hand-off when the monarch or the initiative holder is eliminated', () => {
  const sourceFor = (state: GameState, seat: number): number =>
    [4, 3, 2, 1].find((id) => id !== seat && seatOf(state, id).eliminated_at_seq === null)!;

  interface Killer {
    name: string;
    reason: string;
    events: (seat: number, source: number) => EventEnvelope[];
    by: (source: number) => number | null;
  }
  // Every eliminating event that exists: life_changed to 0, player_conceded, player_eliminated,
  // commander_damage by tally and by life, and poison_changed.
  const KILLERS: Killer[] = [
    {
      name: 'life_changed to 0',
      reason: 'life',
      events: (seat, source) => [lc(seat, -40, source)],
      by: (source) => source,
    },
    {
      name: 'player_conceded',
      reason: 'concession',
      events: (seat) => [concede(seat)],
      by: () => null,
    },
    {
      name: 'player_eliminated',
      reason: 'declared',
      events: (seat) => [declare(seat, 'mill')],
      by: () => null,
    },
    {
      name: 'commander_damage by tally',
      reason: 'commander_damage',
      events: (seat, source) => [cd(source, 0, seat, 21)],
      by: (source) => source,
    },
    {
      name: 'commander_damage by life',
      reason: 'life',
      events: (seat, source) => [lc(seat, -35), cd(source, 0, seat, 5)],
      by: (source) => source,
    },
    {
      name: 'poison_changed',
      reason: 'poison',
      events: (seat, source) => [pc(seat, 10, source)],
      by: (source) => source,
    },
  ];

  interface Scenario {
    name: string;
    /** The state just before the kill, the holder named by `hold`. */
    build: (hold: (seat: number) => EventEnvelope) => GameState;
    seat: number;
    /** The holder is the last seat: it acts as its own source. */
    self?: boolean;
    expected: number | null;
  }
  const SCENARIOS: Scenario[] = [
    {
      name: 'seat 3 holds it and seat 1 is the active player: seat 1 takes it (criterion 4)',
      build: (hold) => run(game(), hold(3)),
      seat: 3,
      expected: 1,
    },
    {
      name: 'the holder is the active player: the next seat in turn order takes it',
      build: (hold) => run(game({ first: 3 }), hold(3)),
      seat: 3,
      expected: 4,
    },
    {
      name: 'the holder is the active player at the end of order: the scan wraps to the start',
      build: (hold) => run(game({ first: 4 }), hold(4)),
      seat: 4,
      expected: 1,
    },
    {
      name: 'the next seat skips a seat eliminated earlier',
      build: (hold) => run(game(), concede(2), hold(1)),
      seat: 1,
      expected: 3,
    },
    {
      name: 'the wrap-around skips a seat eliminated earlier',
      build: (hold) => run(game({ first: 4 }), concede(1), hold(4)),
      seat: 4,
      expected: 2,
    },
    {
      name: 'the active seat was eliminated earlier (still active): the next seat after it takes it',
      build: (hold) => run(game(), concede(1), hold(3)),
      seat: 3,
      expected: 2,
    },
    {
      name: 'a seat_moved correction changed order to [3,2,1,4]: the new order is read',
      build: (hold) => run(game(), moved(1, 3, true), hold(1)),
      seat: 1,
      expected: 4,
    },
    {
      name: 'after the correction the standing active seat takes it wherever it sits in order',
      build: (hold) => run(game(), moved(1, 3, true), hold(4)),
      seat: 4,
      expected: 1,
    },
    {
      name: 'after turn_passed the new active player takes it',
      build: (hold) => run(game(), hold(3), turn()),
      seat: 3,
      expected: 2,
    },
    {
      name: 'in round 2 the active seat is seat 1 again and takes it from seat 3',
      build: (hold) => run(game(), turn(), turn(), turn(), turn(), hold(3)),
      seat: 3,
      expected: 1,
    },
    {
      name: 'the last seat in the game is eliminated: nobody holds it',
      build: (hold) => run(game({ count: 2 }), concede(1), hold(2)),
      seat: 2,
      self: true,
      expected: null,
    },
    {
      name: 'a holder that is not the one eliminated keeps it',
      build: (hold) => run(game(), hold(2)),
      seat: 3,
      expected: 2,
    },
  ];

  const CASES = SCENARIOS.flatMap((s) =>
    MONARCH_KEYS.flatMap((key) => KILLERS.map((k) => [key, k.name, s.name, s, k] as const)),
  );

  it.each(CASES)('%s, eliminated by %s: %s', (key, _killer, _scenario, scenario, killer) => {
    const before = scenario.build(holderSet(key));
    const source = scenario.self ? scenario.seat : sourceFor(before, scenario.seat);
    const after = run(before, ...killer.events(scenario.seat, source));
    expect(after[key]).toBe(scenario.expected);
    const other = key === 'monarch' ? 'initiative' : 'monarch';
    expect(after[other]).toBeNull();
    if (scenario.expected !== null) {
      expect(seatOf(after, scenario.expected).eliminated_at_seq).toBeNull();
    }
    // The elimination happened with this event's reason, eliminator and seq.
    expect(seatOf(after, scenario.seat)).toMatchObject({
      elimination_reason: killer.reason,
      eliminated_by: killer.by(source),
      eliminated_at_seq: after.last_seq,
      eliminated_round: before.round,
    });
    // Nothing else moved: not the phase, the active seat, the turn, the round, the order, the seats.
    expect(
      without(after, 'seats', 'monarch', 'initiative', 'last_seq', 'no_winner', 'commander_damage'),
    ).toBe(
      without(
        before,
        'seats',
        'monarch',
        'initiative',
        'last_seq',
        'no_winner',
        'commander_damage',
      ),
    );
    for (const s of before.seats) {
      if (s.seat !== scenario.seat) expect(seatOf(after, s.seat)).toEqual(s);
    }
    expect(after.no_winner).toBe(scenario.self === true);
  });

  const TO_WALK = KILLERS.map((k) => [k.name, k] as const);

  describe.each(TO_WALK)('both keys at once, eliminated by %s', (_name, killer) => {
    const kill = (state: GameState, seat: number) =>
      run(state, ...killer.events(seat, sourceFor(state, seat)));

    it('one seat holds both and both pass to the active seat together (4.7.6 row 8)', () => {
      const after = kill(run(game(), monarchSet(3), initiativeSet(3)), 3);
      expect([after.monarch, after.initiative]).toEqual([1, 1]);
    });

    it('one seat holds both and is the active player: both go to the next seat together (4.7.6 row 2)', () => {
      const after = kill(run(game({ first: 3 }), monarchSet(3), initiativeSet(3)), 3);
      expect([after.monarch, after.initiative]).toEqual([4, 4]);
    });

    it('monarch 3, initiative 4: eliminating seat 3 moves only the monarch (4.7.6 row 7)', () => {
      const after = kill(run(game(), monarchSet(3), initiativeSet(4)), 3);
      expect([after.monarch, after.initiative]).toEqual([1, 4]);
    });

    it('monarch 3, initiative 4: eliminating seat 4 moves only the initiative', () => {
      const after = kill(run(game(), monarchSet(3), initiativeSet(4)), 4);
      expect([after.monarch, after.initiative]).toEqual([3, 1]);
    });

    it('monarch 2, initiative 4: eliminating seat 3 changes neither (4.7.6 row 6)', () => {
      const after = kill(run(game(), monarchSet(2), initiativeSet(4)), 3);
      expect([after.monarch, after.initiative]).toEqual([2, 4]);
    });

    it('nobody holds either: they stay null', () => {
      const after = kill(game(), 3);
      expect([after.monarch, after.initiative]).toEqual([null, null]);
    });

    it('both are held by the last seat, which is eliminated: both become null', () => {
      const before = run(game({ count: 2 }), concede(1), monarchSet(2), initiativeSet(2));
      const after = run(before, ...killer.events(2, 2));
      expect([after.monarch, after.initiative]).toEqual([null, null]);
      expect(after.no_winner).toBe(true);
    });
  });

  it('criterion 4 as worded: seat 3 is the monarch, seat 1 is the active player, seat 3 is eliminated: seat 1 is the monarch', () => {
    const before = run(game(), monarchSet(3));
    expect([before.monarch, before.active_seat]).toEqual([3, 1]);
    const after = run(before, lc(3, -40, 2));
    expect(seatOf(after, 3).eliminated_at_seq).not.toBeNull();
    expect(after.monarch).toBe(1);
  });

  it('criterion 4, second sentence: the monarch is the active player, so the next non-eliminated seat takes it', () => {
    const before = run(game({ first: 3 }), monarchSet(3), initiativeSet(3));
    expect(before.active_seat).toBe(3);
    const after = run(before, concede(3));
    expect([after.monarch, after.initiative]).toEqual([4, 4]);
    expect(after.active_seat).toBe(3);
  });

  it('then turn_passed and further events behave: the crown stays, the eliminated active seat is skipped', () => {
    let state = run(game(), monarchSet(3), concede(3));
    expect(state.monarch).toBe(1);
    state = run(state, concede(1));
    expect([state.monarch, state.active_seat]).toEqual([2, 1]);
    state = run(state, turn());
    expect([state.active_seat, state.turn, state.monarch]).toEqual([2, 2, 2]);
    state = run(state, concede(2));
    expect([state.monarch, state.active_seat]).toEqual([4, 2]);
    state = run(state, turn());
    expect([state.active_seat, state.turn, state.round, state.monarch]).toEqual([4, 3, 1, 4]);
    expect(codeOf(state, monarchSet(3))).toBe('seat_eliminated');
    expect(run(state, monarchSet(4)).monarch).toBe(4);
    const last = run(state, concede(4));
    expect(last.monarch).toBeNull();
    expect(last.no_winner).toBe(true);
  });

  it('the hand-off hands the crown on again each time its holder is eliminated', () => {
    const state = run(game(), monarchSet(3), concede(3), concede(1), concede(2));
    expect(state.monarch).toBe(4);
    expect(inGame(state)).toEqual([4]);
  });

  it('the holder is eliminated while the other crown is held by the active seat: the active seat holds both', () => {
    const after = run(game(), initiativeSet(1), monarchSet(3), concede(3));
    expect([after.monarch, after.initiative]).toEqual([1, 1]);
  });

  it('a monarch_set after the hand-off by the new holder, then an elimination, hands off again', () => {
    const state = run(game(), monarchSet(3), concede(3), monarchSet(2), lc(2, -40, 4));
    expect(state.monarch).toBe(1);
  });

  it('a seat_moved correction after the crown was set changes who receives it, not who holds it', () => {
    const state = run(game(), monarchSet(1), moved(1, 3, true));
    expect(state.monarch).toBe(1);
    expect(state.order).toEqual([3, 2, 1, 4]);
    expect(run(state, concede(1)).monarch).toBe(4);
  });

  describe('4.5.2 step 4 on the routine itself, with several seats at once', () => {
    const entry = (seat: number): Elimination => ({ seat, reason: 'life', by: null, cause: null });
    const base = (): GameState => run(game(), monarchSet(2), initiativeSet(3));

    it('4.7.6 row 10: seats 1 (active), 2 and 3 out together: both go to seat 4', () => {
      const after = eliminate(base(), 20, [entry(1), entry(2), entry(3)]);
      expect([after.monarch, after.initiative]).toEqual([4, 4]);
    });

    it('4.7.6 row 11: every seat out: both become null', () => {
      const after = eliminate(base(), 20, [entry(1), entry(2), entry(3), entry(4)]);
      expect([after.monarch, after.initiative]).toEqual([null, null]);
      expect(after.no_winner).toBe(true);
    });

    it('entries that name a seat twice or an eliminated seat do not hand anything off twice', () => {
      const state = outAt(base(), 4, 3);
      const after = eliminate(state, 20, [entry(2), entry(2), entry(4)]);
      expect([after.monarch, after.initiative]).toEqual([1, 3]);
    });

    it('a call that eliminates nobody leaves the crowns', () => {
      const after = eliminate(base(), 20, []);
      expect([after.monarch, after.initiative]).toEqual([2, 3]);
    });
  });
});

// ---------------------------------------------------------------------------------------------
describe('check order (4.7): shape, phase, unknown_seat, seat_eliminated, then the handler rules', () => {
  interface Ctx {
    state: GameState;
    payload: Payload;
  }
  type Maker = (c: Ctx) => Ctx;

  /**
   * One violation per check of the parsed list, in the same order; they do not interfere. The
   * violation of the latest check is applied first, so an earlier one that overwrites the same
   * field wins.
   */
  function suite(
    label: string,
    type: string,
    codes: string[],
    makers: Maker[],
    base: () => Ctx,
  ): void {
    describe(label, () => {
      it('has one violation maker per listed check', () => {
        expect(makers).toHaveLength(codes.length);
      });

      it(`every set of simultaneous violations gives the earliest check that fails (${(1 << codes.length) - 1} sets)`, () => {
        const failures: string[] = [];
        for (let mask = 1; mask < 1 << codes.length; mask++) {
          const picked = codes.map((_, i) => i).filter((i) => (mask & (1 << i)) !== 0);
          let ctx = base();
          for (const i of [...picked].reverse()) ctx = makers[i]!(ctx);
          const actual = codeOf(ctx.state, ev(type, ctx.payload));
          const want = codes[picked[0]!]!;
          if (actual !== want)
            failures.push(`${picked.map((i) => i + 1).join('+')} gave ${actual}`);
        }
        expect(failures).toEqual([]);
      });

      it('each single violation gives its own code, and no violation is accepted', () => {
        expect(codeOf(base().state, ev(type, base().payload))).toBe('ok');
        codes.forEach((code, i) => {
          const ctx = makers[i]!(base());
          expect(codeOf(ctx.state, ev(type, ctx.payload)), `check ${i + 1}`).toBe(code);
        });
      });
    });
  }

  suite(
    'poison_changed',
    'poison_changed',
    CHECKS_471,
    [
      (c) => ({ ...c, payload: { ...c.payload, delta: '5' } }),
      (c) => ({ ...c, state: inPhase(c.state, 'finished') }),
      (c) => ({ ...c, payload: { ...c.payload, source_seat: 7 } }),
      (c) => ({ ...c, state: outAt(c.state, 3, 5) }),
      (c) => ({ ...c, payload: { ...c.payload, delta: 0 } }),
      (c) => ({ ...c, state: withSeat(c.state, 3, { poison: '3' }) }),
    ],
    () => ({ state: game(), payload: { seat: 3, delta: 5, source_seat: 2 } }),
  );

  suite(
    'counter_changed',
    'counter_changed',
    CHECKS_472,
    [
      (c) => ({ ...c, payload: { ...c.payload, counter: 'poison' } }),
      (c) => ({ ...c, state: inPhase(c.state, 'abandoned') }),
      (c) => ({ ...c, payload: { ...c.payload, seat: 7 } }),
      (c) => ({ ...c, state: outAt(c.state, 3, 5) }),
      (c) => ({ ...c, payload: { ...c.payload, delta: 1000 } }),
      (c) => ({ ...c, state: withSeat(c.state, 3, { energy: '5' }) }),
    ],
    () => ({ state: game(), payload: { seat: 3, counter: 'energy', delta: 5 } }),
  );

  suite(
    'commander_cast',
    'commander_cast',
    CHECKS_473,
    [
      (c) => ({ ...c, payload: { ...c.payload, delta: '1' } }),
      (c) => ({ ...c, state: inPhase(c.state, 'lobby') }),
      (c) => ({ ...c, payload: { ...c.payload, seat: 7 } }),
      (c) => ({ ...c, state: outAt(c.state, 2, 5) }),
      (c) => ({ ...c, payload: { ...c.payload, delta: 2 } }),
      (c) => ({ ...c, payload: { ...c.payload, commander: 5 } }),
      (c) => ({ ...c, state: withSeat(c.state, 2, { commander_cast: ['x', 0] }) }),
    ],
    () => ({ state: game({ partners: [2] }), payload: { seat: 2, commander: 0, delta: 1 } }),
  );

  for (const type of ['monarch_set', 'initiative_set']) {
    suite(
      type,
      type,
      CHECKS_474,
      [
        (c) => ({ ...c, payload: { ...c.payload, seat: '3' } }),
        (c) => ({ ...c, state: inPhase(c.state, 'finished') }),
        (c) => ({ ...c, payload: { ...c.payload, seat: 7 } }),
        (c) => ({ ...c, state: outAt(c.state, 3, 5) }),
      ],
      () => ({ state: game(), payload: { seat: 3 } }),
    );
  }

  it('a bad payload in a closed game is invalid_payload, and an unknown seat in one is invalid_phase', () => {
    const closed = inPhase(game(), 'finished');
    expect(codeOf(closed, pc(2, 'x'))).toBe('invalid_payload');
    expect(codeOf(closed, pc(7, 1))).toBe('invalid_phase');
    expect(codeOf(closed, monarchSet(null))).toBe('invalid_payload');
    expect(codeOf(closed, monarchSet(7))).toBe('invalid_phase');
  });

  it('seat_eliminated covers the source seat of poison_changed whatever the sign of the delta', () => {
    const state = run(game(), concede(3));
    expect(codeOf(state, pc(2, 1, 3))).toBe('seat_eliminated');
    expect(codeOf(state, pc(2, -1, 3))).toBe('seat_eliminated');
    expect(codeOf(state, pc(2, 0, 3))).toBe('seat_eliminated');
  });

  it('unknown_seat names the seat before the source: both unknown, and a known seat with an unknown source', () => {
    const state = game({ count: 3 });
    expect(codeOf(state, pc(5, 1, 6))).toBe('unknown_seat');
    expect(codeOf(state, pc(1, 1, 6))).toBe('unknown_seat');
  });

  it('a rejected event changes nothing: the state is the same bytes', () => {
    const state = run(game({ partners: [2] }), monarchSet(3), pc(2, 3));
    const snapshot = canon(state);
    const bad = [
      pc(2, 0),
      pc(2, -4),
      pc(7, 1),
      cc(2, 'energy', -1),
      cc(2, 'poison', 1),
      cast(2, 1, -1),
      cast(2, 5, 1),
      monarchSet(null),
      initiativeSet(7),
    ];
    for (const e of bad) {
      expect(apply(state, e).ok).toBe(false);
      expect(canon(state)).toBe(snapshot);
    }
  });
});

// ---------------------------------------------------------------------------------------------
describe('the worked examples of 4.7, row by row (parsed so the tables and the code cannot drift)', () => {
  interface Spec {
    /** Numbers the result cell must mention. */
    mentions?: number[];
    /** The code the result cell must name; every event of `bad` must give it. */
    code?: string;
    bad?: Array<[GameState, EventEnvelope]>;
    check?: () => void;
  }
  const CODE_IN_CELL = /`(invalid_[a-z]+|seat_eliminated|unknown_[a-z]+)`/;

  function verify(rows: string[][], specs: Record<number, Spec>, n: number): void {
    const spec = specs[n]!;
    const result = rows[n - 1]![3]!;
    for (const m of spec.mentions ?? []) {
      expect(result, `row ${n} mentions ${m}`).toMatch(new RegExp(`(^|[^0-9])-?${m}([^0-9]|$)`));
    }
    if (spec.code !== undefined) {
      expect(result).toContain(`\`${spec.code}\``);
      for (const [state, e] of spec.bad!) expect(codeOf(state, e)).toBe(spec.code);
    } else {
      expect(result).not.toMatch(CODE_IN_CELL);
      spec.check!();
    }
  }

  const out = (state: GameState, id: number, reason: string, by: number | null) =>
    expect(seatOf(state, id)).toMatchObject({ elimination_reason: reason, eliminated_by: by });
  const staysIn = (state: GameState, id: number) =>
    expect(seatOf(state, id).eliminated_at_seq).toBeNull();

  describe('4.7.1 poison_changed', () => {
    const SPECS: Record<number, Spec> = {
      1: {
        mentions: [10, 4, 1],
        check: () => {
          const before = run(game(), pc(2, 9));
          const s = run(before, pc(2, 1, 4));
          expect(seatOf(s, 2)).toMatchObject({
            poison: 10,
            elimination_reason: 'poison',
            eliminated_by: 4,
            eliminated_at_seq: before.last_seq + 1,
            eliminated_round: 1,
          });
        },
      },
      2: {
        mentions: [15, 14],
        check: () => {
          const at14 = run(game({ poisonThreshold: 15 }), pc(2, 14));
          staysIn(at14, 2);
          const s = run(at14, pc(2, 1));
          expect(seatOf(s, 2).poison).toBe(15);
          out(s, 2, 'poison', null);
        },
      },
      3: {
        mentions: [12],
        check: () => {
          const s = run(game(), pc(2, 12));
          expect(seatOf(s, 2).poison).toBe(12);
          out(s, 2, 'poison', null);
        },
      },
      4: {
        mentions: [1],
        check: () => {
          const s = run(game(), pc(2, 4), pc(2, -3, 1));
          expect(seatOf(s, 2).poison).toBe(1);
          staysIn(s, 2);
        },
      },
      5: { code: 'invalid_amount', bad: [[run(game(), pc(2, 2)), pc(2, -3)]] },
      6: { code: 'invalid_amount', bad: [[game(), pc(2, -1)]] },
      7: {
        mentions: [11],
        check: () => {
          const s = run(withSeat(game(), 2, { poison: 12 }), pc(2, -1));
          expect(seatOf(s, 2).poison).toBe(11);
          staysIn(s, 2);
        },
      },
      8: {
        mentions: [13],
        check: () => {
          const s = run(withSeat(game(), 2, { poison: 12 }), pc(2, 1));
          expect(seatOf(s, 2).poison).toBe(13);
          out(s, 2, 'poison', null);
        },
      },
      9: {
        mentions: [10, 3],
        check: () => {
          const s = run(game(), pc(3, 10, 3));
          expect(seatOf(s, 3).poison).toBe(10);
          out(s, 3, 'poison', 3);
        },
      },
      10: {
        code: 'invalid_amount',
        bad: [0, 1000, -1000].map((d): [GameState, EventEnvelope] => [game(), pc(2, d)]),
      },
      11: {
        code: 'invalid_payload',
        bad: [
          ...['1', true, null, 1.5].map((d): [GameState, EventEnvelope] => [game(), pc(2, d)]),
          [game(), make('poison_changed', { seat: 2 })],
        ],
      },
      12: {
        code: 'seat_eliminated',
        bad: [
          [run(game(), concede(2)), pc(2, 0)],
          [run(game(), concede(2)), pc(1, -1, 2)],
        ],
      },
      13: {
        code: 'invalid_amount',
        bad: [1, -1].map((d): [GameState, EventEnvelope] => [
          rawGame({ poison_threshold: '10' }),
          pc(2, d),
        ]),
      },
      14: {
        mentions: [1],
        check: () => {
          const s = run(rawGame({ poison_threshold: 0 }), pc(2, 1));
          expect(seatOf(s, 2).poison).toBe(1);
          out(s, 2, 'poison', null);
        },
      },
      15: { code: 'invalid_amount', bad: [[withSeat(game(), 2, { poison: MAX_SAFE }), pc(2, 1)]] },
      16: { code: 'invalid_phase', bad: [[lobbyOf(), pc(2, 1)]] },
    };

    it('has a spec for each of the sixteen rows', () => {
      expect(Object.keys(SPECS).map(Number)).toEqual(Array.from({ length: 16 }, (_, i) => i + 1));
    });
    it.each(Array.from({ length: 16 }, (_, i) => [i + 1]))('row %i', (n) =>
      verify(ROWS_471, SPECS, n),
    );
  });

  describe('4.7.2 counter_changed', () => {
    const SPECS: Record<number, Spec> = {
      1: {
        mentions: [3],
        check: () => {
          const s = run(game(), cc(2, 'energy', 3));
          expect([seatOf(s, 2).energy, seatOf(s, 2).experience]).toEqual([3, 0]);
        },
      },
      2: {
        mentions: [3],
        check: () => {
          const s = run(game(), cc(2, 'energy', 4), cc(2, 'energy', -1));
          expect(seatOf(s, 2).energy).toBe(3);
        },
      },
      3: {
        mentions: [0],
        check: () => {
          const s = run(game(), cc(3, 'experience', 2), cc(3, 'experience', -2));
          expect(seatOf(s, 3).experience).toBe(0);
        },
      },
      4: { code: 'invalid_amount', bad: [[run(game(), cc(2, 'energy', 1)), cc(2, 'energy', -2)]] },
      5: {
        code: 'invalid_amount',
        bad: [0, 1000].map((d): [GameState, EventEnvelope] => [game(), cc(2, 'energy', d)]),
      },
      6: { code: 'invalid_payload', bad: [[game(), cc(2, 'poison', 1)]] },
      7: { code: 'seat_eliminated', bad: [[run(game(), concede(2)), cc(2, 'energy', 0)]] },
      8: {
        code: 'invalid_amount',
        bad: [[withSeat(game(), 2, { energy: MAX_SAFE }), cc(2, 'energy', 1)]],
      },
      9: {
        code: 'invalid_amount',
        bad: [[withSeat(game(), 2, { energy: '3' }), cc(2, 'energy', 1)]],
      },
    };

    it('has a spec for each of the nine rows', () => {
      expect(Object.keys(SPECS).map(Number)).toEqual(Array.from({ length: 9 }, (_, i) => i + 1));
    });
    it.each(Array.from({ length: 9 }, (_, i) => [i + 1]))('row %i', (n) =>
      verify(ROWS_472, SPECS, n),
    );
  });

  describe('4.7.3 commander_cast', () => {
    const SPECS: Record<number, Spec> = {
      1: {
        mentions: [0, 1, 2, 4],
        check: () => {
          const s1 = run(game({ partners: [2] }), cast(2, 1, 1));
          const s2 = run(s1, cast(2, 1, 1));
          expect(seatOf(s1, 2).commander_cast).toEqual([0, 1]);
          expect(seatOf(s2, 2).commander_cast).toEqual([0, 2]);
          expect(2 * seatOf(s2, 2).commander_cast[1]!).toBe(4);
          expect(2 * seatOf(s2, 2).commander_cast[0]!).toBe(0);
        },
      },
      2: {
        mentions: [0, 1],
        check: () => {
          const base = run(game({ partners: [2] }), cast(2, 1, 1), cast(2, 1, 1));
          expect(seatOf(base, 2).commander_cast).toEqual([0, 2]);
          const s1 = run(base, cast(2, 1, -1));
          const s2 = run(s1, cast(2, 1, -1));
          expect(seatOf(s1, 2).commander_cast).toEqual([0, 1]);
          expect(seatOf(s2, 2).commander_cast).toEqual([0, 0]);
        },
      },
      3: { code: 'invalid_amount', bad: [[game({ partners: [2] }), cast(2, 1, -1)]] },
      4: {
        mentions: [1],
        check: () => {
          const s = run(game(), cast(3, 0, 1));
          expect(seatOf(s, 3).commander_cast).toEqual([1]);
        },
      },
      5: {
        code: 'invalid_amount',
        bad: [0, 2, -2].map((d): [GameState, EventEnvelope] => [
          game({ partners: [2] }),
          cast(2, 0, d),
        ]),
      },
      6: { code: 'unknown_commander', bad: [[game(), cast(3, 1, 1)]] },
      7: { code: 'invalid_amount', bad: [[game(), cast(3, 5, 2)]] },
      8: { code: 'unknown_commander', bad: [[game(), cast(3, 1, -1)]] },
      9: { code: 'seat_eliminated', bad: [[run(game(), concede(2)), cast(2, 9, 0)]] },
      10: {
        code: 'invalid_payload',
        bad: [
          [game(), cast(2, '0', 1)],
          [game(), make('commander_cast', { seat: 2, commander: 0 })],
        ],
      },
    };

    it('has a spec for each of the ten rows', () => {
      expect(Object.keys(SPECS).map(Number)).toEqual(Array.from({ length: 10 }, (_, i) => i + 1));
    });
    it.each(Array.from({ length: 10 }, (_, i) => [i + 1]))('row %i', (n) =>
      verify(ROWS_473, SPECS, n),
    );
  });

  describe('4.7.6 the hand-off', () => {
    /** The two values the "after" cell starts with: `null` as null, a missing second one as null. */
    function expected(n: number): [number | null, number | null] {
      const cell = ROWS_476[n - 1]![3]!;
      const head = /^((?:\d+|`null`)(?:, (?:\d+|`null`))?)/.exec(cell)![1]!.split(', ');
      const value = (t: string | undefined): number | null =>
        t === undefined || t === '`null`' ? null : Number(t);
      return [value(head[0]), value(head[1])];
    }

    /** Eliminate `seat` with each eliminating event that exists, from the state `before`. */
    const SEAT_KILLERS: Array<[string, (seat: number, source: number) => EventEnvelope[]]> = [
      ['life_changed', (seat, source) => [lc(seat, -40, source)]],
      ['player_conceded', (seat) => [concede(seat)]],
      ['player_eliminated', (seat) => [declare(seat)]],
      ['commander_damage by tally', (seat, source) => [cd(source, 0, seat, 21)]],
      ['commander_damage by life', (seat, source) => [lc(seat, -35), cd(source, 0, seat, 5)]],
      ['poison_changed', (seat, source) => [pc(seat, 10, source)]],
    ];

    interface Row {
      /** The state before the elimination, holders already set through events. */
      before: () => GameState;
      seat: number;
      source: number;
    }
    const ROW_SETUPS: Record<number, Row> = {
      1: { before: () => run(game(), monarchSet(3)), seat: 3, source: 2 },
      2: {
        before: () => run(game({ first: 3 }), monarchSet(3), initiativeSet(3)),
        seat: 3,
        source: 2,
      },
      3: { before: () => run(game({ first: 4 }), monarchSet(4)), seat: 4, source: 2 },
      4: { before: () => run(game(), concede(2), monarchSet(1)), seat: 1, source: 3 },
      5: { before: () => run(game(), concede(1), monarchSet(3)), seat: 3, source: 2 },
      6: { before: () => run(game(), monarchSet(2), initiativeSet(4)), seat: 3, source: 1 },
      7: { before: () => run(game(), monarchSet(3), initiativeSet(4)), seat: 3, source: 1 },
      8: { before: () => run(game(), monarchSet(3), initiativeSet(3)), seat: 3, source: 1 },
      9: { before: () => run(game(), moved(1, 3, true), monarchSet(1)), seat: 1, source: 2 },
      12: {
        before: () => ({
          ...outAt(game(), 2, 3),
          monarch: 2,
        }),
        seat: 4,
        source: 3,
      },
      13: {
        before: () => ({
          ...run(game(), monarchSet(2)),
          order: [4, 3, 2, 1],
          active_seat: null,
        }),
        seat: 2,
        source: 1,
      },
    };

    const DIRECT = new Set([10, 11]);
    const withKiller = [1, 2, 3, 4, 5, 6, 7, 8, 9, 12, 13].flatMap((n) =>
      SEAT_KILLERS.map((k) => [n, k[0], k[1]] as const),
    );

    it.each(withKiller)('row %i, seat out by %s', (n, _name, events) => {
      const row = ROW_SETUPS[n]!;
      const before = row.before();
      // A cell with one value leaves the second key null: no scenario sets an initiative then.
      const after = run(before, ...events(row.seat, row.source));
      expect([after.monarch, after.initiative]).toEqual(expected(n));
    });

    it('row 10: seats 1 (active), 2 and 3 out together: 4, 4 (the routine is called directly)', () => {
      expect(DIRECT.has(10)).toBe(true);
      const start = run(game(), monarchSet(2), initiativeSet(3));
      const after = eliminate(
        start,
        20,
        [1, 2, 3].map((seat) => ({ seat, reason: 'life', by: null, cause: null })),
      );
      expect([after.monarch, after.initiative]).toEqual(expected(10));
    });

    it('row 11: every seat out: null, null (the routine is called directly)', () => {
      expect(DIRECT.has(11)).toBe(true);
      const start = run(game(), monarchSet(2), initiativeSet(4));
      const after = eliminate(
        start,
        20,
        [1, 2, 3, 4].map((seat) => ({ seat, reason: 'life', by: null, cause: null })),
      );
      expect([after.monarch, after.initiative]).toEqual(expected(11));
    });

    it('the table gives the expected pairs the scenarios above are compared with', () => {
      expect(Array.from({ length: 13 }, (_, i) => expected(i + 1))).toEqual([
        [1, null],
        [4, 4],
        [1, null],
        [3, null],
        [2, null],
        [2, 4],
        [1, 4],
        [1, 1],
        [4, null],
        [4, 4],
        [null, null],
        [2, null],
        [4, null],
      ]);
    });
  });
});

// ---------------------------------------------------------------------------------------------
describe('phases: lobby, finished and abandoned reject; active and ending accept', () => {
  const HITS: Array<[string, () => EventEnvelope]> = [
    ['poison_changed', () => pc(2, 1)],
    ['counter_changed', () => cc(2, 'energy', 1)],
    ['commander_cast', () => cast(2, 0, 1)],
    ['monarch_set', () => monarchSet(2)],
    ['initiative_set', () => initiativeSet(2)],
  ];

  it.each(HITS)('%s in the lobby is invalid_phase, and nothing changes', (_type, hit) => {
    const lobby = lobbyOf();
    const snapshot = canon(lobby);
    expect(apply(lobby, hit())).toEqual({ ok: false, error: { code: 'invalid_phase' } });
    expect(canon(lobby)).toBe(snapshot);
    expect(lobby.seats.map((s) => [s.poison, s.energy, s.experience, s.commander_cast])).toEqual(
      Array.from({ length: 4 }, () => [0, 0, 0, [0]]),
    );
    expect([lobby.monarch, lobby.initiative]).toEqual([null, null]);
  });

  it.each(
    HITS.flatMap(([type, hit]) =>
      (['finished', 'abandoned'] as const).map((p) => [type, p, hit] as const),
    ),
  )('%s in %s is invalid_phase', (_type, phase, hit) => {
    expect(codeOf(inPhase(game(), phase), hit())).toBe('invalid_phase');
  });

  it.each(HITS)('%s in active is accepted', (_type, hit) => {
    expect(codeOf(game(), hit())).toBe('ok');
  });

  describe.each(HITS)('%s in ending (hand-built)', (_type, hit) => {
    it('is accepted, stays in ending and has the effect it has in active', () => {
      const inActive = run(game(), hit());
      const inEnding = run(inPhase(game(), 'ending'), hit());
      expect(inEnding.phase).toBe('ending');
      expect(canon(inEnding)).toBe(canon({ ...inActive, phase: 'ending' }));
    });
  });

  it('ending: poison eliminates and the crown moves on, as in active', () => {
    const start = inPhase(run(game(), monarchSet(3), initiativeSet(4)), 'ending');
    const after = run(start, pc(3, 10, 2));
    expect(seatOf(after, 3)).toMatchObject({ elimination_reason: 'poison', eliminated_by: 2 });
    expect([after.monarch, after.initiative, after.phase]).toEqual([1, 4, 'ending']);
  });

  it('ending: the other rules hold', () => {
    const ending = inPhase(game(), 'ending');
    expect(codeOf(ending, pc(7, 1))).toBe('unknown_seat');
    expect(codeOf(ending, pc(2, 0))).toBe('invalid_amount');
    expect(codeOf(ending, cc(2, 'poison', 1))).toBe('invalid_payload');
    expect(codeOf(ending, cast(2, 1, 1))).toBe('unknown_commander');
    expect(codeOf(run(ending, concede(2)), monarchSet(2))).toBe('seat_eliminated');
  });

  it('a closed game takes no hand-off either: the events that would cause it are invalid_phase', () => {
    const state = inPhase(run(game(), monarchSet(3)), 'finished');
    expect(codeOf(state, concede(3))).toBe('invalid_phase');
    expect(codeOf(state, pc(3, 10))).toBe('invalid_phase');
  });
});

// ---------------------------------------------------------------------------------------------
describe('only what this event raised can eliminate (4.5.2, "Reason precedence")', () => {
  /** Seat 2 sits at 12 poison, above the threshold of 10, and is still in the game. */
  const poisoned = (): GameState => withSeat(game({ partners: [2] }), 2, { poison: 12 });

  it('a negative poison delta never eliminates, even above the threshold (4.7.1 row 7)', () => {
    const after = run(poisoned(), pc(2, -1));
    expect(seatOf(after, 2)).toMatchObject({ poison: 11, eliminated_at_seq: null });
  });

  it('removing 2 leaves a seat exactly at the threshold in the game: 12 to 10 is not an elimination', () => {
    expect(seatOf(run(poisoned(), pc(2, -2)), 2)).toMatchObject({
      poison: 10,
      eliminated_at_seq: null,
    });
  });

  it('a positive poison delta eliminates it, because this event raised it (4.7.1 row 8)', () => {
    const after = run(poisoned(), pc(2, 1, 4));
    expect(seatOf(after, 2)).toMatchObject({
      poison: 13,
      elimination_reason: 'poison',
      eliminated_by: 4,
    });
  });

  it('counter_changed, in either direction, does not eliminate it', () => {
    for (const counter of ['energy', 'experience']) {
      const up = run(poisoned(), cc(2, counter, 3));
      expect(seatOf(up, 2).eliminated_at_seq).toBeNull();
      expect(seatOf(run(up, cc(2, counter, -3)), 2).eliminated_at_seq).toBeNull();
    }
  });

  it('commander_cast, in either direction, does not eliminate it', () => {
    const up = run(poisoned(), cast(2, 0, 1), cast(2, 1, 1));
    expect(seatOf(up, 2).eliminated_at_seq).toBeNull();
    expect(seatOf(run(up, cast(2, 0, -1)), 2).eliminated_at_seq).toBeNull();
  });

  it('monarch_set and initiative_set naming it do not eliminate it', () => {
    const after = run(poisoned(), monarchSet(2), initiativeSet(2));
    expect(seatOf(after, 2).eliminated_at_seq).toBeNull();
    expect([after.monarch, after.initiative]).toEqual([2, 2]);
  });

  it('a life gain does not eliminate it', () => {
    const after = run(poisoned(), lc(2, 5));
    expect(seatOf(after, 2)).toMatchObject({ life: 45, poison: 12, eliminated_at_seq: null });
  });

  it('a life loss that leaves life above 0 does not eliminate it either', () => {
    const after = run(poisoned(), lc(2, -5, 3));
    expect(seatOf(after, 2)).toMatchObject({ life: 35, poison: 12, eliminated_at_seq: null });
  });

  it('commander damage that leaves life above 0 and the tally below the threshold does not eliminate it', () => {
    const after = run(poisoned(), cd(1, 0, 2, 5));
    expect(seatOf(after, 2)).toMatchObject({ life: 35, poison: 12, eliminated_at_seq: null });
  });

  it('a poison above the threshold on a seat that takes a positive delta of another counter stays in the game', () => {
    const after = run(poisoned(), cc(2, 'energy', 1), pc(3, 1, 2));
    expect(seatOf(after, 2).eliminated_at_seq).toBeNull();
    expect(seatOf(after, 3)).toMatchObject({ poison: 1, eliminated_at_seq: null });
  });

  it('a seat at life 0 in the game (hand-built) is not eliminated by a poison delta that stays below the threshold', () => {
    const state = withSeat(game(), 2, { life: 0 });
    const after = run(state, pc(2, 3), cc(2, 'energy', 2), cast(2, 0, 1));
    expect(seatOf(after, 2)).toMatchObject({ life: 0, poison: 3, eliminated_at_seq: null });
  });

  it('a commander tally at the threshold in the game (hand-built) is not eliminated by poison below its threshold', () => {
    const state: GameState = { ...game(), commander_damage: { '2': { '1:0': 25 } } };
    const after = run(state, pc(2, 3));
    expect(seatOf(after, 2)).toMatchObject({ poison: 3, eliminated_at_seq: null });
  });

  it('poison never gives a holder a crown by itself: a seat above the threshold keeps its crown when untouched', () => {
    const state = withSeat(run(game(), monarchSet(2)), 2, { poison: 12 });
    const after = run(state, pc(2, -1));
    expect(after.monarch).toBe(2);
  });
});

// ---------------------------------------------------------------------------------------------
describe('stored numbers that are not integers, and the safe range (4.7)', () => {
  it.each([
    ['a string', '3'],
    ['null', null],
    ['true', true],
    ['a fraction', 1.5],
    ['an array', [3]],
  ])('poison stored as %s is invalid_amount for a gain and for a removal', (_label, stored) => {
    const state = withSeat(game(), 2, { poison: stored });
    expect(codeOf(state, pc(2, 1))).toBe('invalid_amount');
    expect(codeOf(state, pc(2, -1))).toBe('invalid_amount');
  });

  it.each([
    ['a string', '10'],
    ['null', null],
    ['true', true],
    ['a fraction', 10.5],
  ])(
    'poison_threshold stored as %s is invalid_amount for a gain and for a removal',
    (_label, stored) => {
      const state = withSettings(run(game(), pc(2, 3)), { poison_threshold: stored });
      expect(codeOf(state, pc(2, 1))).toBe('invalid_amount');
      expect(codeOf(state, pc(2, -1))).toBe('invalid_amount');
    },
  );

  it('a threshold stored wrongly does not matter to the other handlers', () => {
    const state = withSettings(game(), { poison_threshold: '10' });
    expect(codeOf(state, cc(2, 'energy', 1))).toBe('ok');
    expect(codeOf(state, cast(2, 0, 1))).toBe('ok');
    expect(codeOf(state, monarchSet(2))).toBe('ok');
  });

  it.each(['energy', 'experience'])(
    '%s stored as a string is invalid_amount, and the other counter still works',
    (name) => {
      const other = name === 'energy' ? 'experience' : 'energy';
      const state = withSeat(game(), 2, { [name]: '3' });
      expect(codeOf(state, cc(2, name, 1))).toBe('invalid_amount');
      expect(codeOf(state, cc(2, other, 1))).toBe('ok');
    },
  );

  it.each([
    ['poison', () => pc(2, 1)],
    ['energy', () => cc(2, 'energy', 1)],
    ['experience', () => cc(2, 'experience', 1)],
  ])('%s at 2^53 - 1 plus 1 is invalid_amount, and one below plus 1 is accepted', (field, hit) => {
    expect(codeOf(withSeat(game(), 2, { [field]: MAX_SAFE }), hit())).toBe('invalid_amount');
    const edge = withSeat(withSettings(game(), { poison_threshold: 99 }), 2, {
      [field]: MAX_SAFE - 1,
    });
    expect(codeOf(edge, hit())).toBe('ok');
  });

  it('a cast count at 2^53 - 1 plus 1 is invalid_amount; an entry that is not an integer is too', () => {
    expect(codeOf(withSeat(game(), 2, { commander_cast: [MAX_SAFE] }), cast(2, 0, 1))).toBe(
      'invalid_amount',
    );
    expect(codeOf(withSeat(game(), 2, { commander_cast: ['1'] }), cast(2, 0, 1))).toBe(
      'invalid_amount',
    );
    expect(codeOf(withSeat(game(), 2, { commander_cast: [MAX_SAFE - 1] }), cast(2, 0, 1))).toBe(
      'ok',
    );
  });

  it('a cast entry that is missing for the index is invalid_amount (4.7.3 check 7)', () => {
    expect(
      codeOf(withSeat(game({ partners: [2] }), 2, { commander_cast: [0] }), cast(2, 1, 1)),
    ).toBe('invalid_amount');
  });
});

// ---------------------------------------------------------------------------------------------
describe('purity: neither the input state nor the event is mutated', () => {
  const holding = (): GameState =>
    run(game({ partners: [2] }), monarchSet(3), initiativeSet(3), pc(4, 2));
  const EVENTS: EventEnvelope[] = [
    pc(2, 3),
    pc(3, 10, 2),
    pc(2, -1),
    pc(2, 0),
    pc(7, 1),
    cc(2, 'energy', 4),
    cc(2, 'experience', -1),
    cc(2, 'poison', 1),
    cast(2, 1, 1),
    cast(2, 1, -1),
    cast(3, 1, 1),
    monarchSet(2),
    initiativeSet(4),
    monarchSet(null),
    concede(3),
    lc(3, -40, 2),
  ];

  it.each(EVENTS.map((e, i) => [i, e.type, JSON.stringify(e.payload), e] as const))(
    'frozen state and event survive #%i %s %s, accepted or rejected',
    (_i, _type, _payload, e) => {
      const state = deepFreeze(holding());
      const frozen = deepFreeze(structuredClone(e));
      const snapshot = canon(state);
      const result = apply(state, frozen);
      expect(canon(state)).toBe(snapshot);
      expect(JSON.stringify(frozen)).toBe(JSON.stringify(e));
      if (result.ok) expect(result.state).not.toBe(state);
    },
  );

  it('the same event on the same state gives the same result twice', () => {
    const state = holding();
    const a = apply(state, pc(3, 10, 2));
    const b = apply(state, pc(3, 10, 2));
    expect(canonicalJson(a.ok ? a.state : null)).toBe(canonicalJson(b.ok ? b.state : null));
  });

  it('commander_cast does not alias the commander_cast list of the input', () => {
    const state = game({ partners: [2] });
    const result = apply(state, cast(2, 1, 1));
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    const before = seatOf(state, 2).commander_cast;
    const now = seatOf(result.state, 2).commander_cast;
    expect(now).not.toBe(before);
    expect(before).toEqual([0, 0]);
    expect(now).toEqual([0, 1]);
    // Writing into the new list later cannot reach the input.
    now[0] = 99;
    expect(seatOf(state, 2).commander_cast).toEqual([0, 0]);
  });

  it('a successful event does not alias the seats array or the seat of the input', () => {
    const state = game();
    for (const e of [pc(2, 3), cc(2, 'energy', 1), cast(2, 0, 1), concede(2)]) {
      const result = apply(state, e);
      expect(result.ok && result.state.seats).not.toBe(state.seats);
      expect(result.ok && result.state.seats.find((s) => s.seat === 2)).not.toBe(seatOf(state, 2));
    }
    expect(seatOf(state, 2)).toMatchObject({
      poison: 0,
      energy: 0,
      experience: 0,
      commander_cast: [0],
    });
  });

  it('a hand-off does not change the order array or the input holders', () => {
    const state = holding();
    const order = state.order;
    const result = apply(state, concede(3));
    expect(result.ok && result.state.order).toEqual(order);
    expect([state.monarch, state.initiative]).toEqual([3, 3]);
  });
});

// ---------------------------------------------------------------------------------------------
describe('oracle: the five handlers and the hand-off against a function written from 4.1 and 4.4 to 4.7', () => {
  // Park-Miller generator: the same numbers in the Python twin, so both run the same cases.
  let seed = 615;
  const next = (): number => {
    seed = (seed * 48271) % 2147483647;
    return seed;
  };
  const pick = <T>(items: readonly T[]): T => items[next() % items.length]!;

  const ABSENT = Symbol('absent');
  const ids = (n: number): number[] => Array.from({ length: n }, (_, i) => i + 1);
  const PHASES = [
    'active',
    'active',
    'active',
    'active',
    'active',
    'active',
    'ending',
    'ending',
    'lobby',
    'finished',
    'abandoned',
  ] as const;
  const COUNTS = [3, 4, 4, 5, 6] as const;
  const POISONS: unknown[] = [0, 0, 0, 0, 2, 5, 9, 9, 12, MAX_SAFE - 2, '3'];
  const ENERGIES: unknown[] = [0, 0, 0, 1, 3, 7, MAX_SAFE - 1, null];
  const CASTS: unknown[] = [0, 0, 0, 1, 1, 3, MAX_SAFE - 1, 'x'];
  const LIVES: unknown[] = [40, 40, 40, 25, 10, 5, 1];
  const POISON_THRESHOLDS: unknown[] = [10, 10, 10, 10, 10, 5, 15, 1, 0, 99, '10'];
  const CD_THRESHOLDS: unknown[] = [21, 21, 21, 21, 5, 1, '21'];
  const TYPES = [
    'poison_changed',
    'poison_changed',
    'poison_changed',
    'poison_changed',
    'counter_changed',
    'counter_changed',
    'commander_cast',
    'commander_cast',
    'monarch_set',
    'monarch_set',
    'initiative_set',
    'initiative_set',
    'life_changed',
    'life_changed',
    'player_conceded',
    'player_eliminated',
    'commander_damage',
    'commander_damage',
    'turn_passed',
    'seat_moved',
  ] as const;
  const POISON_DELTAS: unknown[] = [
    1, 1, 2, 3, 5, 8, 9, 10, 12, -1, -2, -3, -5, 0, 999, 1000, -999, -1000, '1', null, true, 1.5,
    ABSENT,
  ]; // prettier-ignore
  const COUNTER_DELTAS: unknown[] = [
    1,
    2,
    3,
    5,
    -1,
    -2,
    -3,
    0,
    999,
    1000,
    -1000,
    '1',
    null,
    ABSENT,
  ];
  const COUNTERS: unknown[] = [
    'energy', 'energy', 'experience', 'experience', 'poison', 'Energy', '', 1, null, ABSENT,
  ]; // prettier-ignore
  const CAST_DELTAS: unknown[] = [1, 1, 1, -1, -1, 0, 2, -2, '1', null, ABSENT];
  const CAST_INDEXES: unknown[] = [0, 0, 0, 1, 1, 2, -1, '0', null, ABSENT];
  const LIFE_DELTAS: unknown[] = [-1, -3, -5, -10, -40, -35, 5, 10, 0, 1000, '1', ABSENT];
  const AMOUNTS: unknown[] = [1, 2, 5, 7, 12, 21, 25, 0, -1, 1000, '5', ABSENT];
  const CAUSES: unknown[] = [ABSENT, ABSENT, 'mill', 'combo', 'Mill', null];
  const CORRECTIONS: unknown[] = [true, true, true, ABSENT, false];
  const D5 = [
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
  ];

  const seatPool = (n: number): unknown[] => [
    ...ids(n),
    ...ids(n),
    ...ids(n),
    7,
    0,
    9,
    '2',
    null,
    ABSENT,
  ];
  const sourcePool = (n: number): unknown[] => [ABSENT, ABSENT, ABSENT, null, ...ids(n), 7, 0, '1'];
  const holderPool = (n: number): unknown[] => [null, null, ...ids(n)];

  const BASES = new Map<number, GameState>(COUNTS.map((n) => [n, game({ count: n })]));

  const isInt = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v);
  const isSeatId = (v: unknown): v is number => isInt(v) && v >= 1 && v <= 8;
  const isAbsent = (v: unknown): boolean => v === undefined || v === null;

  interface Case {
    state: GameState;
    steps: Array<{ type: string; payload: Payload }>;
    label: string;
  }

  function genEvent(count: number): { type: string; payload: Payload } {
    const type = pick(TYPES);
    const payload: Payload = {};
    const put = (key: string, value: unknown): void => {
      if (value !== ABSENT) payload[key] = value;
    };
    switch (type) {
      case 'poison_changed': {
        const seat = pick(seatPool(count));
        const delta = pick(POISON_DELTAS);
        const source = pick(sourcePool(count));
        put('seat', seat);
        put('delta', delta);
        put('source_seat', source);
        break;
      }
      case 'counter_changed': {
        const seat = pick(seatPool(count));
        const counter = pick(COUNTERS);
        const delta = pick(COUNTER_DELTAS);
        put('seat', seat);
        put('counter', counter);
        put('delta', delta);
        break;
      }
      case 'commander_cast': {
        const seat = pick(seatPool(count));
        const commander = pick(CAST_INDEXES);
        const delta = pick(CAST_DELTAS);
        put('seat', seat);
        put('commander', commander);
        put('delta', delta);
        break;
      }
      case 'monarch_set':
      case 'initiative_set': {
        put('seat', pick(seatPool(count)));
        break;
      }
      case 'life_changed': {
        const seat = pick(seatPool(count));
        const delta = pick(LIFE_DELTAS);
        const source = pick(sourcePool(count));
        put('seat', seat);
        put('delta', delta);
        put('source_seat', source);
        break;
      }
      case 'player_conceded': {
        put('seat', pick(seatPool(count)));
        break;
      }
      case 'player_eliminated': {
        const seat = pick(seatPool(count));
        const cause = pick(CAUSES);
        put('seat', seat);
        put('cause', cause);
        break;
      }
      case 'commander_damage': {
        const source = pick(seatPool(count));
        const commander = pick(CAST_INDEXES);
        const target = pick(seatPool(count));
        const amount = pick(AMOUNTS);
        put('source_seat', source);
        put('commander', commander);
        put('target_seat', target);
        put('amount', amount);
        break;
      }
      case 'turn_passed':
        break;
      case 'seat_moved': {
        const from = pick(seatPool(count));
        const to = pick(seatPool(count));
        const correction = pick(CORRECTIONS);
        put('from', from);
        put('to', to);
        put('correction', correction);
        break;
      }
    }
    return { type, payload };
  }

  function build(): Case {
    const phase = pick(PHASES);
    const count = pick(COUNTS);
    const order = ids(count);
    for (let i = next() % 3; i > 0; i--) {
      const a = next() % count;
      const b = next() % count;
      [order[a], order[b]] = [order[b]!, order[a]!];
    }
    const lone = next() % 5 === 0;
    const loneSeat = 1 + (next() % count);
    const out = ids(count).map(() => next() % 4 === 0);
    const partner = ids(count).map(() => next() % 3 === 0);
    const active = 1 + (next() % count);
    const first = 1 + (next() % count);
    const round = 1 + (next() % 3);
    const turnNumber = 1 + (next() % 9);
    let monarch = pick(holderPool(count)) as number | null;
    let initiative = pick(holderPool(count)) as number | null;
    const loneMonarch = next() % 2 === 0;
    const loneInitiative = next() % 2 === 0;
    const poisons = ids(count).map(() => pick(POISONS));
    const energies = ids(count).map(() => pick(ENERGIES));
    const experiences = ids(count).map(() => pick(ENERGIES));
    const casts = ids(count).map(() => [pick(CASTS), pick(CASTS)]);
    const lives = ids(count).map(() => pick(LIVES));
    const poisonThreshold = pick(POISON_THRESHOLDS);
    const cdThreshold = pick(CD_THRESHOLDS);
    if (lone && loneMonarch) monarch = loneSeat;
    if (lone && loneInitiative) initiative = loneSeat;
    const seats = BASES.get(count)!.seats.map((s, i) => {
      const isOut = lone ? s.seat !== loneSeat : out[i]!;
      return {
        ...s,
        life: isOut ? 0 : lives[i],
        poison: poisons[i],
        energy: energies[i],
        experience: experiences[i],
        commanders: partner[i]! ? PARTNERS : SOLO,
        commander_cast: partner[i]! ? casts[i] : [casts[i]![0]],
        ...(isOut
          ? {
              elimination_reason: 'life',
              eliminated_by: null,
              eliminated_at_seq: 3,
              eliminated_round: 1,
              elimination_cause: null,
            }
          : {}),
      } as Seat;
    });
    const base = BASES.get(count)!;
    const state = inPhase(
      {
        ...base,
        order,
        seats,
        active_seat: active,
        first_seat: first,
        round,
        turn: turnNumber,
        monarch,
        initiative,
        settings: {
          ...base.settings,
          poison_threshold: poisonThreshold,
          commander_damage_threshold: cdThreshold,
        } as GameState['settings'],
      },
      phase,
    );
    const steps = Array.from({ length: 2 + (next() % 5) }, () => genEvent(count));
    return {
      state,
      steps,
      label: `${phase} n=${count} order=${order.join('')} active=${active} first=${first} monarch=${monarch} initiative=${initiative} pt=${JSON.stringify(poisonThreshold)}`,
    };
  }

  // --- the oracle, from the contract's words -------------------------------------------------

  /** The seats after `from` in `order`, cyclically, `from` itself last (4.4.10, "Next seat"). */
  function scanAfter(p: Plain, from: number | null): number[] {
    const at = from === null ? -1 : p.order.indexOf(from);
    return [...p.order.slice(at + 1), ...p.order.slice(0, at + 1)];
  }
  const standingIds = (p: Plain): number[] =>
    p.seats.filter((s) => s.eliminated_at_seq === null).map((s) => s.seat);

  interface Entry {
    seat: number;
    reason: string;
    by: number | null;
    cause: string | null;
  }

  /** 4.5.2, steps 1 to 4, on `p` after the event's own changes (`p.last_seq` is the event's seq). */
  function eliminateIn(p: Plain, entries: Entry[]): void {
    const standing = standingIds(p);
    const applied: number[] = [];
    for (const e of entries) {
      if (!standing.includes(e.seat) || applied.includes(e.seat)) continue;
      applied.push(e.seat);
      const row = p.seats.find((s) => s.seat === e.seat)!;
      row.elimination_reason = e.reason;
      row.eliminated_by = e.by;
      row.elimination_cause = e.cause;
      row.eliminated_at_seq = p.last_seq;
      row.eliminated_round = p.round;
    }
    if (applied.length > 0 && applied.length === standing.length) p.no_winner = true;
    const survivors = standing.filter((id) => !applied.includes(id));
    for (const key of MONARCH_KEYS) {
      const holder = p[key];
      if (holder === null || !applied.includes(holder)) continue;
      if (survivors.length === 0) p[key] = null;
      else if (p.active_seat !== null && survivors.includes(p.active_seat)) p[key] = p.active_seat;
      else p[key] = scanAfter(p, p.active_seat).find((id) => survivors.includes(id)) ?? null;
    }
  }

  /** The expected outcome: an error code, or the expected state. */
  function oracle(before: Plain, type: string, payload: Payload): string | Plain {
    const p = JSON.parse(JSON.stringify(before)) as Plain;
    const row = (id: unknown): PSeat | undefined => p.seats.find((s) => s.seat === id);
    const isOut = (id: unknown): boolean => row(id)!.eliminated_at_seq !== null;
    const live = p.phase === 'active' || p.phase === 'ending';
    const sourceOk = (v: unknown): boolean => isAbsent(v) || isSeatId(v);
    const sourceGiven = (v: unknown): v is number => !isAbsent(v);
    p.last_seq += 1;
    switch (type) {
      case 'poison_changed': {
        const { seat, delta, source_seat: source } = payload;
        if (!isSeatId(seat) || !isInt(delta) || !sourceOk(source)) return 'invalid_payload';
        if (!live) return 'invalid_phase';
        if (row(seat) === undefined || (sourceGiven(source) && row(source) === undefined)) {
          return 'unknown_seat';
        }
        if (isOut(seat) || (sourceGiven(source) && isOut(source))) return 'seat_eliminated';
        if (delta === 0 || Math.abs(delta) > 999) return 'invalid_amount';
        const target = row(seat)!;
        const threshold = p.settings.poison_threshold;
        if (!isInt(target.poison) || !isInt(threshold)) return 'invalid_amount';
        const poison = target.poison + delta;
        if (poison < 0 || poison > MAX_SAFE) return 'invalid_amount';
        target.poison = poison;
        if (delta > 0 && poison >= threshold) {
          eliminateIn(p, [
            { seat, reason: 'poison', by: sourceGiven(source) ? source : null, cause: null },
          ]);
        }
        return p;
      }
      case 'counter_changed': {
        const { seat, counter, delta } = payload;
        if (!isSeatId(seat) || !isInt(delta)) return 'invalid_payload';
        if (counter !== 'energy' && counter !== 'experience') return 'invalid_payload';
        if (!live) return 'invalid_phase';
        if (row(seat) === undefined) return 'unknown_seat';
        if (isOut(seat)) return 'seat_eliminated';
        if (delta === 0 || Math.abs(delta) > 999) return 'invalid_amount';
        const target = row(seat)!;
        const stored = target[counter];
        if (!isInt(stored) || stored + delta < 0 || stored + delta > MAX_SAFE)
          return 'invalid_amount';
        target[counter] = stored + delta;
        return p;
      }
      case 'commander_cast': {
        const { seat, commander, delta } = payload;
        if (!isSeatId(seat) || !isInt(commander) || !isInt(delta)) return 'invalid_payload';
        if (!live) return 'invalid_phase';
        if (row(seat) === undefined) return 'unknown_seat';
        if (isOut(seat)) return 'seat_eliminated';
        if (delta !== 1 && delta !== -1) return 'invalid_amount';
        const target = row(seat)!;
        if (commander < 0 || commander >= target.commanders.length) return 'unknown_commander';
        const count = target.commander_cast[commander];
        if (!isInt(count) || count + delta < 0 || count + delta > MAX_SAFE) return 'invalid_amount';
        target.commander_cast[commander] = count + delta;
        return p;
      }
      case 'monarch_set':
      case 'initiative_set': {
        const { seat } = payload;
        if (!isSeatId(seat)) return 'invalid_payload';
        if (!live) return 'invalid_phase';
        if (row(seat) === undefined) return 'unknown_seat';
        if (isOut(seat)) return 'seat_eliminated';
        p[type === 'monarch_set' ? 'monarch' : 'initiative'] = seat;
        return p;
      }
      case 'life_changed': {
        const { seat, delta, source_seat: source } = payload;
        if (!isSeatId(seat) || !isInt(delta) || !sourceOk(source)) return 'invalid_payload';
        if (!live) return 'invalid_phase';
        if (row(seat) === undefined || (sourceGiven(source) && row(source) === undefined)) {
          return 'unknown_seat';
        }
        if (isOut(seat) || (sourceGiven(source) && isOut(source))) return 'seat_eliminated';
        if (delta === 0 || Math.abs(delta) > 999) return 'invalid_amount';
        const target = row(seat)!;
        if (!isInt(target.life) || Math.abs(target.life + delta) > MAX_SAFE)
          return 'invalid_amount';
        target.life += delta;
        if (delta < 0 && target.life <= 0) {
          eliminateIn(p, [
            { seat, reason: 'life', by: sourceGiven(source) ? source : null, cause: null },
          ]);
        }
        return p;
      }
      case 'player_conceded': {
        const { seat } = payload;
        if (!isSeatId(seat)) return 'invalid_payload';
        if (!live) return 'invalid_phase';
        if (row(seat) === undefined) return 'unknown_seat';
        if (isOut(seat)) return 'seat_eliminated';
        eliminateIn(p, [{ seat, reason: 'concession', by: null, cause: null }]);
        return p;
      }
      case 'player_eliminated': {
        const { seat, cause } = payload;
        if (!isSeatId(seat)) return 'invalid_payload';
        if (!isAbsent(cause) && !D5.includes(cause as string)) return 'invalid_payload';
        if (!live) return 'invalid_phase';
        if (row(seat) === undefined) return 'unknown_seat';
        if (isOut(seat)) return 'seat_eliminated';
        eliminateIn(p, [
          { seat, reason: 'declared', by: null, cause: isAbsent(cause) ? null : (cause as string) },
        ]);
        return p;
      }
      case 'commander_damage': {
        const { source_seat: s, commander: c, target_seat: t, amount: a } = payload;
        if (!isSeatId(s) || !isSeatId(t) || !isInt(c) || !isInt(a)) return 'invalid_payload';
        if (!live) return 'invalid_phase';
        if (row(s) === undefined || row(t) === undefined) return 'unknown_seat';
        if (isOut(t) || isOut(s)) return 'seat_eliminated';
        if (a < 1 || a > 999) return 'invalid_amount';
        if (c < 0 || c >= row(s)!.commanders.length) return 'unknown_commander';
        const target = row(t)!;
        const threshold = p.settings.commander_damage_threshold;
        const key = `${s}:${c}`;
        const tally = (p.commander_damage[String(t)]?.[key] ?? 0) + a;
        if (!isInt(target.life) || !isInt(threshold)) return 'invalid_amount';
        if (Math.abs(target.life - a) > MAX_SAFE || tally > MAX_SAFE) return 'invalid_amount';
        p.commander_damage[String(t)] = { ...p.commander_damage[String(t)], [key]: tally };
        target.life -= a;
        if (tally >= threshold)
          eliminateIn(p, [{ seat: t, reason: 'commander_damage', by: s, cause: null }]);
        else if (target.life <= 0)
          eliminateIn(p, [{ seat: t, reason: 'life', by: s, cause: null }]);
        return p;
      }
      case 'turn_passed': {
        if (!live) return 'invalid_phase';
        if (standingIds(p).length === 0) return 'invalid_phase';
        const scan = scanAfter(p, p.active_seat);
        const n = scan.find((id) => !isOut(id))!;
        const walked = scan.slice(0, scan.indexOf(n) + 1);
        p.active_seat = n;
        p.turn += 1;
        if (walked.includes(p.first_seat!)) p.round += 1;
        return p;
      }
      case 'seat_moved': {
        const { from, to, correction } = payload;
        if (!isSeatId(from) || !isSeatId(to)) return 'invalid_payload';
        if (!isAbsent(correction) && typeof correction !== 'boolean') return 'invalid_payload';
        const allowed = p.phase === 'lobby' || (live && correction === true);
        if (!allowed) return 'invalid_phase';
        if (row(from) === undefined) return 'unknown_seat';
        if (row(to) === undefined) return 'unknown_seat';
        const a = p.order.indexOf(from);
        const b = p.order.indexOf(to);
        [p.order[a], p.order[b]] = [p.order[b]!, p.order[a]!];
        return p;
      }
    }
    throw new Error(`the oracle has no rule for ${type}`);
  }

  const CASES = 3000;

  it(`agrees with the reducer on ${CASES} generated cases, whole states after every step`, () => {
    const seen = new Map<string, number>();
    const bumpSeen = (kind: string): void => void seen.set(kind, (seen.get(kind) ?? 0) + 1);
    const mismatches: string[] = [];
    const FIVE = [
      'poison_changed',
      'counter_changed',
      'commander_cast',
      'monarch_set',
      'initiative_set',
    ];
    for (let i = 0; i < CASES; i++) {
      const c = build();
      let state = c.state;
      for (const step of c.steps) {
        const beforePlain = plain(state);
        const expected = oracle(beforePlain, step.type, step.payload);
        const result = apply(state, make(step.type, step.payload));
        const actual = result.ok ? canon(result.state) : result.error.code;
        const want = typeof expected === 'string' ? expected : canonicalJson(expected);
        if (actual !== want) {
          if (mismatches.length < 5) {
            mismatches.push(
              `${c.label} ${step.type} ${JSON.stringify(step.payload)}\n  want ${want}\n  got  ${actual}`,
            );
          }
          break;
        }
        if (typeof expected === 'string') {
          if (FIVE.includes(step.type)) bumpSeen(`${step.type}:${expected}`);
          else bumpSeen(`other:${expected}`);
          continue;
        }
        bumpSeen(`ok:${step.type}`);
        for (const e of expected.seats) {
          if (e.eliminated_at_seq === expected.last_seq) {
            bumpSeen(`eliminated_by_${e.elimination_reason}`);
            if (step.type === 'poison_changed') bumpSeen('poison_elimination');
          }
        }
        if (expected.no_winner && !beforePlain.no_winner) bumpSeen('full_draw');
        for (const key of MONARCH_KEYS) {
          const was = beforePlain[key];
          if (was === null || expected[key] === was) continue;
          const lost = expected.seats.find((s) => s.seat === was)!;
          if (lost.eliminated_at_seq !== expected.last_seq) continue;
          const kind =
            expected[key] === null
              ? 'handoff_null'
              : expected[key] === expected.active_seat
                ? 'handoff_active'
                : 'handoff_walk';
          bumpSeen(kind);
          bumpSeen(`handoff_by_${lost.elimination_reason}`);
          bumpSeen(`handoff_${key}`);
        }
        state = (result as { ok: true; state: GameState }).state;
      }
    }
    expect(mismatches).toEqual([]);
    const required = [
      'ok:poison_changed',
      'ok:counter_changed',
      'ok:commander_cast',
      'ok:monarch_set',
      'ok:initiative_set',
      'ok:life_changed',
      'ok:player_conceded',
      'ok:player_eliminated',
      'ok:commander_damage',
      'ok:turn_passed',
      'ok:seat_moved',
      ...['poison_changed', 'counter_changed'].flatMap((t) =>
        [
          'invalid_payload',
          'invalid_phase',
          'unknown_seat',
          'seat_eliminated',
          'invalid_amount',
        ].map((code) => `${t}:${code}`),
      ),
      ...[
        'invalid_payload',
        'invalid_phase',
        'unknown_seat',
        'seat_eliminated',
        'invalid_amount',
        'unknown_commander',
      ].map((code) => `commander_cast:${code}`),
      ...['monarch_set', 'initiative_set'].flatMap((t) =>
        ['invalid_payload', 'invalid_phase', 'unknown_seat', 'seat_eliminated'].map(
          (code) => `${t}:${code}`,
        ),
      ),
      'poison_elimination',
      'eliminated_by_poison',
      'full_draw',
      'handoff_active',
      'handoff_walk',
      'handoff_null',
      'handoff_monarch',
      'handoff_initiative',
      'handoff_by_life',
      'handoff_by_concession',
      'handoff_by_declared',
      'handoff_by_commander_damage',
      'handoff_by_poison',
    ];
    for (const kind of required) {
      expect(seen.get(kind) ?? 0, `the generator never produced ${kind}`).toBeGreaterThan(0);
    }
  });
});

// ---------------------------------------------------------------------------------------------
describe('the vectors of S-6.1.5 are named after their content (8.6) and hold the criteria', () => {
  const dir = new URL('../vectors/', import.meta.url);
  const NAME =
    /^(accept|reject)-(poison-changed|counter-changed|commander-cast|monarch-set|initiative-set)-.*\.json$|^accept-hand-off-.*\.json$/;
  const files = readdirSync(dir).filter((f) => NAME.test(f));
  const TYPE_OF: Record<string, string> = {
    'poison-changed': 'poison_changed',
    'counter-changed': 'counter_changed',
    'commander-cast': 'commander_cast',
    'monarch-set': 'monarch_set',
    'initiative-set': 'initiative_set',
  };
  interface VEvent {
    type: string;
    payload: Record<string, unknown>;
  }
  interface VSeat {
    seat: number;
    poison: number;
    energy: number;
    experience: number;
    commanders: unknown[];
    commander_cast: number[];
    elimination_reason: string | null;
    eliminated_at_seq: number | null;
  }
  interface VState {
    settings: Record<string, unknown>;
    seats: VSeat[];
    order: number[];
    active_seat: number | null;
    monarch: number | null;
    initiative: number | null;
    no_winner: boolean;
    last_seq: number;
  }
  interface Vector {
    name: string;
    settings: Record<string, unknown>;
    events: VEvent[];
    expect: { state?: VState; error?: { index: number; code: string } };
  }
  const load = (file: string): Vector =>
    JSON.parse(readFileSync(new URL(file, dir), 'utf8')) as Vector;
  const typeOfStem = (stem: string): string | undefined => {
    for (const [k, v] of Object.entries(TYPE_OF)) if (stem.includes(`-${k}-`)) return v;
    return undefined;
  };
  const eliminatedSeats = (s: VState): VSeat[] =>
    s.seats.filter((x) => x.eliminated_at_seq !== null);
  const seatIn = (s: VState, id: unknown): VSeat | undefined => s.seats.find((x) => x.seat === id);
  const REASON_OF_NAME: Record<string, string> = {
    life: 'life',
    concession: 'concession',
    declared: 'declared',
    'commander-damage': 'commander_damage',
    poison: 'poison',
  };

  // The code a reject vector's name promises, written from the names (the part before "-before-").
  function codeFromName(stem: string): string | undefined {
    const head = stem
      .replace(
        /^reject-(poison-changed|counter-changed|commander-cast|monarch-set|initiative-set)-/,
        '',
      )
      .split(/-before-(?!its-)/)[0]!;
    const table: Array<[RegExp, string]> = [
      [/^(seat|source-seat)-unknown$/, 'unknown_seat'],
      [/^(seat|source-seat)-(nine|zero|missing|null|a-string|a-boolean)$/, 'invalid_payload'],
      [
        /^(on-an-eliminated-seat|on-a-seat-that-conceded|an-eliminated-seat|from-an-eliminated-source-seat|removal-from-an-eliminated-source-seat|the-seat-that-held-it-before-its-elimination)$/,
        'seat_eliminated',
      ],
      [/^eliminated-(seat|source)$/, 'seat_eliminated'],
      [/^delta-(zero|1000|minus-1000|2|minus-2)$/, 'invalid_amount'],
      [/^delta-/, 'invalid_payload'],
      [/^counter-/, 'invalid_payload'],
      [
        /^commander-(1-of-a-single|2-of-a-partner|1-after-the-partners-were-replaced|far-too-large|negative)/,
        'unknown_commander',
      ],
      [/^commander-/, 'invalid_payload'],
      [
        /^(below-zero|removal-at-zero|energy-below-zero|experience-at-zero|experience-below-zero-after-a-gain|minus-one-at-zero|minus-one-below-zero-on-a-partner)$/,
        'invalid_amount',
      ],
      [
        /^(threshold-a-string|threshold-null|removal-with-a-placeholder-threshold)$/,
        'invalid_amount',
      ],
      [/^in-the-lobby$/, 'invalid_phase'],
      [/^unknown-commander$/, 'unknown_commander'],
      [/^invalid-amount$/, 'invalid_amount'],
      [/^invalid-phase$/, 'invalid_phase'],
      [/^unknown-seat$/, 'unknown_seat'],
      [/^bad-payload$/, 'invalid_payload'],
    ];
    return table.find(([re]) => re.test(head))?.[1];
  }

  it('finds the vectors of the story: at least 200 for the five types and the hand-off', () => {
    expect(files.length).toBeGreaterThanOrEqual(200);
  });

  it('every vector the contract names exists', () => {
    const named = [
      ...contract.matchAll(
        /`((?:accept|reject)-(?:poison-changed|counter-changed|commander-cast|monarch-set|initiative-set|hand-off)-[a-z0-9-]+)`/g,
      ),
    ].map((m) => `${m[1]}.json`);
    for (const file of named) expect(existsSync(new URL(file, dir)), file).toBe(true);
  });

  it.each(files)('%s', (file) => {
    const vector = load(file);
    const stem = file.replace(/\.json$/, '');
    expect(vector.name).toBe(stem);
    const isHandOff = stem.startsWith('accept-hand-off-');
    const type = isHandOff ? undefined : typeOfStem(stem);
    if (!isHandOff) expect(type, `no type in the name ${stem}`).toBeDefined();
    const created = vector.events.find((e) => e.type === 'game_created');
    const changed = vector.events.find((e) => e.type === 'settings_changed');
    const ofType = vector.events.filter((e) => e.type === type);

    if (stem.startsWith('accept-')) {
      const final = vector.expect.state!;
      expect(final).toBeDefined();
      const gone = eliminatedSeats(final);
      if (type !== undefined)
        expect(ofType.length, 'an event of the named type').toBeGreaterThan(0);

      if (/-stays?-in-the-game|-stay-in-the-game|never-eliminates/.test(stem)) {
        expect(
          gone.map((s) => s.seat),
          'a "stay" vector eliminates nobody',
        ).toEqual([]);
      } else if (
        /-(eliminates|eliminating)-|-eliminates$|-eliminates-/.test(stem) &&
        type === 'poison_changed'
      ) {
        if (!/eliminating-one-of-four|eliminating-the-last-seat/.test(stem)) {
          expect(gone.some((s) => s.elimination_reason === 'poison')).toBe(true);
        }
      }
      if (/eliminating-the-last-seat-is-a-full-draw/.test(stem)) {
        expect(final.no_winner).toBe(true);
        expect(gone).toHaveLength(final.seats.length);
      }
      if (/eliminating-one-of-four-leaves-three-and-no-draw/.test(stem)) {
        expect(final.seats).toHaveLength(4);
        expect(gone).toHaveLength(1);
        expect(final.no_winner).toBe(false);
      }
      const given = /threshold-(\d+)/.exec(stem);
      if (given !== null && type === 'poison_changed') {
        const n = Number(given[1]);
        expect(final.settings.poison_threshold).toBe(n);
        const via = [
          vector.settings.poison_threshold,
          (created?.payload.settings as Record<string, unknown> | undefined)?.poison_threshold,
          changed?.payload.poison_threshold,
        ];
        expect(via).toContain(n);
      }
      if (/from-game-created/.test(stem)) expect(created).toBeDefined();
      if (/from-settings-changed/.test(stem)) expect(changed).toBeDefined();
      if (/delta-999-in-both-directions/.test(stem)) {
        const deltas = ofType.map((e) => e.payload.delta);
        expect(deltas, 'a +999 and a -999').toEqual(expect.arrayContaining([999, -999]));
      }
      if (/delta-999-both-ways/.test(stem)) {
        const deltas = ofType.map((e) => e.payload.delta);
        expect(deltas).toEqual(expect.arrayContaining([999, -999]));
      }
      if (/down-to-exactly-zero/.test(stem)) {
        const field =
          type === 'poison_changed' ? 'poison' : stem.includes('energy') ? 'energy' : 'experience';
        expect(final.seats.map((s) => s[field as 'poison' | 'energy' | 'experience'])).toEqual(
          final.seats.map(() => 0),
        );
        expect(ofType.some((e) => (e.payload.delta as number) < 0)).toBe(true);
      }
      if (/minus-one-goes-down-to-zero/.test(stem)) {
        expect(ofType.some((e) => e.payload.delta === -1)).toBe(true);
        expect(
          final.seats.some((s) => s.commander_cast.includes(0) && s.commanders.length === 2),
        ).toBe(true);
      }
      if (/partner/.test(stem) && type === 'commander_cast') {
        expect(final.seats.some((s) => s.commanders.length === 2)).toBe(true);
        expect(ofType.some((e) => e.payload.commander === 1)).toBe(true);
      }
      if (/from-a-deck-assigned/.test(stem)) {
        expect(vector.events.some((e) => e.type === 'deck_assigned')).toBe(true);
        expect(vector.events.some((e) => e.type === 'commanders_set')).toBe(false);
      }
      if (/ten-casts/.test(stem)) expect(ofType).toHaveLength(10);
      if (/seats-are-independent/.test(stem)) {
        expect(new Set(ofType.map((e) => e.payload.seat)).size).toBeGreaterThanOrEqual(2);
      }
      if (/ignores-unknown-payload-keys/.test(stem)) {
        const known = new Set(['seat', 'delta', 'source_seat', 'counter', 'commander']);
        expect(ofType.some((e) => Object.keys(e.payload).some((k) => !known.has(k)))).toBe(true);
      }
      if (/the-current-holder-again-is-a-no-op/.test(stem)) {
        const seats = ofType.map((e) => e.payload.seat);
        expect(seats).toHaveLength(2);
        expect(seats[0]).toBe(seats[1]);
      }
      if (/moves-to-the-new-seat-and-only-one-seat-holds-it/.test(stem)) {
        const seats = ofType.map((e) => e.payload.seat);
        expect(new Set(seats).size).toBeGreaterThanOrEqual(2);
        const key = type === 'monarch_set' ? 'monarch' : 'initiative';
        expect(final[key]).toBe(seats[seats.length - 1]);
      }
      if (/-is-independent-of-the-/.test(stem)) {
        expect(vector.events.some((e) => e.type === 'monarch_set')).toBe(true);
        expect(vector.events.some((e) => e.type === 'initiative_set')).toBe(true);
        expect(final.monarch).not.toBeNull();
        expect(final.initiative).not.toBeNull();
      }
      if (/-do-not-change$/.test(stem)) {
        expect(vector.events.some((e) => e.type === 'turn_passed')).toBe(true);
        expect(final.active_seat).toBe(2);
      }

      if (isHandOff) {
        const passes =
          /^accept-hand-off-(monarch|initiative)-passes-to-the-active-seat-when-eliminated-by-(life|concession|declared|commander-damage|poison)$/.exec(
            stem,
          );
        const next =
          /^accept-hand-off-(monarch|initiative)-of-the-active-seat-goes-to-the-next-seat-when-eliminated-by-(life|concession|declared|commander-damage|poison)$/.exec(
            stem,
          );
        const m = passes ?? next;
        if (m !== null) {
          const key = m[1] as 'monarch' | 'initiative';
          const other = key === 'monarch' ? 'initiative' : 'monarch';
          expect(final[other]).toBeNull();
          expect(gone).toHaveLength(1);
          expect(gone[0]!.elimination_reason).toBe(REASON_OF_NAME[m[2]!]);
          expect(final[key]).not.toBeNull();
          expect(final[key]).not.toBe(gone[0]!.seat);
          expect(seatIn(final, final[key])!.eliminated_at_seq).toBeNull();
          if (passes !== null) {
            expect(final[key]).toBe(final.active_seat);
          } else {
            // The holder was the active player: the active seat is the one eliminated.
            expect(gone[0]!.seat).toBe(final.active_seat);
            const at = final.order.indexOf(final.active_seat!);
            const scan = [...final.order.slice(at + 1), ...final.order.slice(0, at + 1)];
            expect(final[key]).toBe(
              scan.find((id) => seatIn(final, id)!.eliminated_at_seq === null),
            );
          }
        }
        if (/wraps-around-the-end-of-the-order/.test(stem)) {
          expect(final.active_seat).toBe(final.order[final.order.length - 1]);
          expect(final.active_seat).toBe(gone[gone.length - 1]!.seat);
          const key = /initiative/.test(stem) ? final.initiative : final.monarch;
          const firstStanding = final.order.find(
            (id) => seatIn(final, id)!.eliminated_at_seq === null,
          );
          expect(key).toBe(firstStanding);
        }
        if (/wrap-around-skips-a-seat-that-is-already-out/.test(stem)) {
          expect(final.active_seat).toBe(final.order[final.order.length - 1]);
          expect(seatIn(final, final.order[0])!.eliminated_at_seq).not.toBeNull();
          expect(final.monarch).toBe(final.order[1]);
        }
        if (/the-next-seat-skips-eliminated-seats/.test(stem)) {
          const at = final.order.indexOf(final.active_seat!);
          const after = final.order[(at + 1) % final.order.length]!;
          expect(seatIn(final, after)!.eliminated_at_seq).not.toBeNull();
        }
        if (/a-seat-moved-correction/.test(stem)) {
          const swap = vector.events.find((e) => e.type === 'seat_moved')!;
          expect(swap.payload.correction).toBe(true);
          expect(final.order).not.toEqual([...final.order].sort());
        }
        if (/a-holder-that-is-not-eliminated-keeps-it/.test(stem)) {
          const sets = vector.events.filter(
            (e) => e.type === 'monarch_set' || e.type === 'initiative_set',
          );
          for (const s of sets) {
            const key = s.type === 'monarch_set' ? final.monarch : final.initiative;
            expect(key).toBe(s.payload.seat);
            expect(seatIn(final, s.payload.seat)!.eliminated_at_seq).toBeNull();
          }
          expect(gone).toHaveLength(1);
        }
        if (/^accept-hand-off-the-last-seat/.test(stem)) {
          expect(final.seats.every((s) => s.eliminated_at_seq !== null)).toBe(true);
          expect(final.no_winner).toBe(true);
          expect(final.monarch).toBeNull();
        }
        if (
          /the-last-seat-leaves-nobody-to-hold-the-monarch-and-the-initiative|the-last-seat-eliminated-by-life-leaves-no-monarch-and-no-initiative/.test(
            stem,
          )
        ) {
          expect(final.initiative).toBeNull();
          expect(vector.events.some((e) => e.type === 'initiative_set')).toBe(true);
          expect(vector.events.some((e) => e.type === 'monarch_set')).toBe(true);
        }
        if (/one-seat-holds-both/.test(stem)) {
          expect(final.monarch).not.toBeNull();
          expect(final.monarch).toBe(final.initiative);
        }
        if (/the-self-eliminated-active-holder/.test(stem)) {
          const hit = vector.events.find((e) => e.type === 'life_changed')!;
          expect(hit.payload.seat).toBe(hit.payload.source_seat);
          expect(hit.payload.seat).toBe(final.active_seat);
        }
        if (/the-turn-passed-before-the-elimination/.test(stem)) {
          expect(vector.events.some((e) => e.type === 'turn_passed')).toBe(true);
          expect(final.monarch).toBe(final.active_seat);
        }
        if (/the-active-seat-is-the-first-seat-of-round-2/.test(stem)) {
          expect(vector.events.filter((e) => e.type === 'turn_passed')).toHaveLength(4);
        }
        if (/when-a-commander-hit-takes-the-life-to-zero/.test(stem)) {
          expect(gone.map((s) => s.elimination_reason)).toEqual(['life']);
          expect(vector.events.some((e) => e.type === 'commander_damage')).toBe(true);
        }
      }
    } else {
      expect(vector.expect.error).toBeDefined();
      const { index, code } = vector.expect.error!;
      const rejected = vector.events[index]!;
      expect(rejected.type).toBe(type);
      const promised = codeFromName(stem);
      expect(promised, `no code is promised by the name ${stem}`).toBeDefined();
      expect(code).toBe(promised);
      const head = stem
        .replace(
          /^reject-(poison-changed|counter-changed|commander-cast|monarch-set|initiative-set)-/,
          '',
        )
        .split(/-before-(?!its-)/)[0]!;
      const p = rejected.payload;
      const before = vector.events.slice(0, index);
      // The content the name promises.
      const field = /^source-seat/.test(head)
        ? 'source_seat'
        : /^seat-/.test(head)
          ? 'seat'
          : /^delta-/.test(head)
            ? 'delta'
            : /^commander-/.test(head)
              ? 'commander'
              : /^counter-/.test(head)
                ? 'counter'
                : null;
      const kind =
        field === null ? null : head.replace(/^(source-seat|seat|delta|commander|counter)-/, '');
      const probes: Record<string, (v: unknown, has: boolean) => boolean> = {
        nine: (v) => v === 9,
        zero: (v) => v === 0,
        missing: (_v, has) => !has,
        null: (v, has) => has && v === null,
        'a-string': (v) => typeof v === 'string',
        'a-boolean': (v) => typeof v === 'boolean',
        'a-fraction': (v) => typeof v === 'number' && !Number.isInteger(v),
        'a-number': (v) => typeof v === 'number',
        '1000': (v) => v === 1000,
        'minus-1000': (v) => v === -1000,
        '2': (v) => v === 2,
        'minus-2': (v) => v === -2,
        negative: (v) => typeof v === 'number' && v < 0,
        'far-too-large': (v) => typeof v === 'number' && v >= 1000,
        empty: (v) => v === '',
        unknown: (v) =>
          field === 'counter'
            ? typeof v === 'string' && v !== 'energy' && v !== 'experience'
            : typeof v === 'number' && v >= 1 && v <= 8,
        'wrong-case': (v) => typeof v === 'string' && v !== v.toLowerCase(),
        'beyond-the-safe-range': (v) => typeof v === 'number' && Math.abs(v) > MAX_SAFE,
      };
      if (field !== null && kind !== null && kind in probes) {
        expect(probes[kind]!(p[field], field in p), `${field} of ${stem}`).toBe(true);
      }
      if (/^seat-unknown$|^source-seat-unknown$/.test(head)) {
        const joinedSeats = vector.events
          .filter((e) => e.type === 'seat_joined')
          .map((e) => e.payload.seat);
        expect(joinedSeats).not.toContain(p[field!]);
      }
      if (/^in-the-lobby$/.test(head)) {
        expect(before.some((e) => e.type === 'game_started')).toBe(false);
      }
      if (
        /^(on-an-eliminated-seat|on-a-seat-that-conceded|an-eliminated-seat|the-seat-that-held-it-before-its-elimination)$/.test(
          head,
        ) ||
        /^eliminated-seat$/.test(head)
      ) {
        const killedSeat = before.some(
          (e) =>
            ['player_conceded', 'player_eliminated', 'poison_changed', 'life_changed'].includes(
              e.type,
            ) && e.payload.seat === p.seat,
        );
        expect(killedSeat, 'the seat was eliminated by an earlier event').toBe(true);
      }
      if (
        /^(from-an-eliminated-source-seat|removal-from-an-eliminated-source-seat|eliminated-source)$/.test(
          head,
        )
      ) {
        expect(
          before.some(
            (e) =>
              ['player_conceded', 'player_eliminated'].includes(e.type) &&
              e.payload.seat === p.source_seat,
          ),
        ).toBe(true);
      }
      if (
        /^(below-zero|removal-at-zero|minus-one-at-zero|energy-below-zero|experience-at-zero|experience-below-zero-after-a-gain|minus-one-below-zero-on-a-partner)$/.test(
          head,
        )
      ) {
        expect(typeof p.delta === 'number' && p.delta < 0).toBe(true);
      }
      if (/^(threshold-a-string|removal-with-a-placeholder-threshold)$/.test(head)) {
        expect(typeof vector.settings.poison_threshold).toBe('string');
      }
      if (/^threshold-null$/.test(head)) expect(vector.settings.poison_threshold).toBeNull();
      if (/^commander-1-after-the-partners-were-replaced$/.test(head)) {
        expect(before.filter((e) => e.type === 'commanders_set')).toHaveLength(2);
      }
      if (/-before-/.test(stem)) {
        // The rejected event breaks the later rule too, or the earlier rule is the one that decides.
        expect(code).toBe(codeFromName(stem));
      }
    }
  });

  it('criterion 1 vectors: 9 poison and +1 from seat 4 eliminates; 15 eliminates only at 15; 0 and 100 are invalid_setting', () => {
    const v = load(
      'accept-poison-changed-ninth-counter-then-the-tenth-eliminates-with-the-source.json',
    );
    expect(v.events.filter((e) => e.type === 'poison_changed').map((e) => e.payload)).toEqual([
      { seat: 2, delta: 9 },
      { seat: 2, delta: 1, source_seat: 4 },
    ]);
    const seat = v.expect.state!.seats.find((s) => s.seat === 2) as VSeat & {
      eliminated_by: number;
    };
    expect(seat).toMatchObject({ poison: 10, elimination_reason: 'poison', eliminated_by: 4 });
    expect(seat.eliminated_at_seq).toBe(v.expect.state!.last_seq);
    for (const stem of [
      'accept-poison-changed-threshold-15-from-game-created-eliminates-only-at-15',
      'accept-poison-changed-threshold-15-from-settings-changed-eliminates-only-at-15',
    ]) {
      const t = load(`${stem}.json`);
      expect(
        t.events.filter((e) => e.type === 'poison_changed').map((e) => e.payload.delta),
      ).toEqual([14, 1]);
      expect(t.expect.state!.settings.poison_threshold).toBe(15);
      expect(seatIn(t.expect.state!, 2)).toMatchObject({
        poison: 15,
        elimination_reason: 'poison',
      });
    }
    const wanted: Record<string, string> = {
      'reject-game-created-poison-threshold-zero': 'invalid_setting',
      'reject-game-created-poison-threshold-100': 'invalid_setting',
      'reject-settings-changed-poison-threshold-zero': 'invalid_setting',
      'reject-settings-changed-poison-threshold-100': 'invalid_setting',
      'reject-poison-changed-below-zero': 'invalid_amount',
      'reject-counter-changed-energy-below-zero': 'invalid_amount',
      'reject-counter-changed-experience-at-zero': 'invalid_amount',
    };
    for (const [stem, code] of Object.entries(wanted)) {
      expect(load(`${stem}.json`).expect.error!.code, stem).toBe(code);
    }
  });

  it('criterion 2 vectors: partner B cast twice has counts [0, 2] with commanders_set and with deck_assigned', () => {
    for (const stem of [
      'accept-commander-cast-partner-b-twice-has-tax-4-and-partner-a-stays-0',
      'accept-commander-cast-partner-b-twice-from-a-deck-assigned',
    ]) {
      const v = load(`${stem}.json`);
      expect(v.events.filter((e) => e.type === 'commander_cast').map((e) => e.payload)).toEqual([
        { seat: 2, commander: 1, delta: 1 },
        { seat: 2, commander: 1, delta: 1 },
      ]);
      expect(seatIn(v.expect.state!, 2)!.commander_cast).toEqual([0, 2]);
    }
    const down = load('accept-commander-cast-minus-one-goes-down-to-zero.json');
    expect(down.events.some((e) => e.payload.delta === -1)).toBe(true);
    expect(load('reject-commander-cast-minus-one-at-zero.json').expect.error!.code).toBe(
      'invalid_amount',
    );
  });

  it('criterion 3 vectors: seat 1 is the monarch, then seat 3: monarch 3, nothing else moved', () => {
    const v = load('accept-monarch-set-moves-to-the-new-seat-and-only-one-seat-holds-it.json');
    expect(v.events.filter((e) => e.type === 'monarch_set').map((e) => e.payload)).toEqual([
      { seat: 1 },
      { seat: 3 },
    ]);
    expect([v.expect.state!.monarch, v.expect.state!.initiative]).toEqual([3, null]);
    const i = load('accept-initiative-set-is-independent-of-the-monarch.json');
    expect([i.expect.state!.monarch, i.expect.state!.initiative]).toEqual([4, 3]);
  });

  it('criterion 4 vectors: seat 3 monarch, seat 1 active, seat 3 out: seat 1; the active holder passes it to seat 4', () => {
    const v = load(
      'accept-hand-off-monarch-passes-to-the-active-seat-when-eliminated-by-life.json',
    );
    expect([v.expect.state!.monarch, v.expect.state!.active_seat]).toEqual([1, 1]);
    const w = load(
      'accept-hand-off-monarch-of-the-active-seat-goes-to-the-next-seat-when-eliminated-by-life.json',
    );
    expect([w.expect.state!.monarch, w.expect.state!.active_seat]).toEqual([4, 3]);
  });

  it('the hand-off has a vector for each eliminating event, for the monarch and the initiative, both cases', () => {
    const has = (re: RegExp) => files.some((f) => re.test(f));
    for (const key of ['monarch', 'initiative']) {
      for (const how of ['life', 'concession', 'declared', 'commander-damage', 'poison']) {
        expect(
          has(
            new RegExp(
              `^accept-hand-off-${key}-passes-to-the-active-seat-when-eliminated-by-${how}\\.json$`,
            ),
          ),
          `${key} ${how}`,
        ).toBe(true);
        expect(
          has(
            new RegExp(
              `^accept-hand-off-${key}-of-the-active-seat-goes-to-the-next-seat-when-eliminated-by-${how}\\.json$`,
            ),
          ),
          `${key} ${how} next`,
        ).toBe(true);
      }
    }
    expect(has(/^accept-hand-off-.*wrap/)).toBe(true);
    expect(has(/^accept-hand-off-.*correction/)).toBe(true);
    expect(has(/^accept-hand-off-.*last-seat/)).toBe(true);
  });

  it('the kinds of task T-6.1.5.3 each have a vector', () => {
    const has = (re: RegExp) => files.some((f) => re.test(f));
    expect(has(/^accept-poison-changed-/)).toBe(true);
    expect(has(/^accept-counter-changed-energy-/)).toBe(true);
    expect(has(/^accept-counter-changed-experience-/)).toBe(true);
    expect(has(/^accept-poison-changed-threshold-15/)).toBe(true);
    expect(has(/^accept-poison-changed-ninth-counter/)).toBe(true);
    expect(has(/^accept-commander-cast-partner/)).toBe(true);
    expect(has(/^accept-monarch-set-/)).toBe(true);
    expect(has(/^accept-initiative-set-/)).toBe(true);
    expect(has(/^accept-hand-off-/)).toBe(true);
  });

  it('every one of the five types has an accept vector with a state and a reject vector', () => {
    for (const k of Object.keys(TYPE_OF)) {
      expect(
        files.some((f) => f.startsWith(`accept-${k}-`)),
        `accept ${k}`,
      ).toBe(true);
      expect(
        files.some((f) => f.startsWith(`reject-${k}-`)),
        `reject ${k}`,
      ).toBe(true);
    }
  });
});
