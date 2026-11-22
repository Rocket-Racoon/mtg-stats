import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { canonicalJson } from './canonical-json';
import { codeOf, joined, lobbyState, make, run, started } from './lobby.test.helpers';
import { apply, replay } from './reducer';
import { ELIMINATION_REASONS, REDUCER_ERROR_CODES } from './types';
import type { EventEnvelope, GameSettings, GameState, Phase, Seat } from './types';

/**
 * Acceptance tests of S-6.1.4 (commander damage per commander source), written from the six
 * acceptance criteria of the story (issue #208) and from CONTRACT.md 3.1 to 3.3, 4.1, 4.2, 4.4.7,
 * 4.4.8, 4.5.1, 4.5.2, 4.6, 5, 6 and 8, not from the handler. The shared vectors are the primary
 * proof for everything reachable through events; this file adds the criteria with their own
 * numbers, built through real events from `game_created`, the check list and the example table
 * parsed from the contract (so the document and the code cannot drift), the phases no log reaches
 * yet, purity, and an oracle written from the contract's words over seeded generated cases.
 *
 * The Python twin is `apps/api/app/games/tests/test_s614_acceptance.py`: same cases, same numbers.
 */

const contract = readFileSync(new URL('../CONTRACT.md', import.meta.url), 'utf8');
const MAX_SAFE = 9007199254740991;

type Payload = Record<string, unknown>;
type Plain = {
  phase: string;
  round: number;
  last_seq: number;
  no_winner: boolean;
  seats: PlainSeat[];
  commander_damage: Record<string, Record<string, number>>;
  settings: Record<string, unknown>;
} & Record<string, unknown>;
type PlainSeat = {
  seat: number;
  life: unknown;
  commanders: unknown[];
  elimination_reason: string | null;
  eliminated_by: number | null;
  eliminated_at_seq: number | null;
  eliminated_round: number | null;
  elimination_cause: string | null;
} & Record<string, unknown>;

const ev = (type: string, payload: Payload = {}): EventEnvelope => make(type, payload);
/** `commander_damage {source_seat, commander, target_seat, amount}`. */
const cd = (source: unknown, commander: unknown, target: unknown, amount: unknown) =>
  make('commander_damage', {
    source_seat: source,
    commander,
    target_seat: target,
    amount,
  });
const lc = (seat: number, delta: number): EventEnvelope => make('life_changed', { seat, delta });
const concede = (seat: number): EventEnvelope => make('player_conceded', { seat });

const PARTNER_A = { card_id: null, name: 'Partner A' };
const PARTNER_B = { card_id: '018f0000-0000-7000-8000-0000000000e2', name: 'Partner B' };
const PARTNERS = [PARTNER_A, PARTNER_B];
const SOLO = [{ card_id: null, name: '' }];

interface Opts {
  count?: number;
  threshold?: number;
  life?: number;
  /** Seats given a partner pair with `commanders_set`. */
  partners?: number[];
  /** Seats given a partner pair with `deck_assigned`. */
  decks?: number[];
}

/** The lobby of a game as a real log: `game_created`, the seats, the commanders, no start. */
function lobbyLog(o: Opts = {}): EventEnvelope[] {
  const settings = {
    starting_life: o.life ?? 40,
    commander_damage_threshold: o.threshold ?? 21,
    poison_threshold: 10,
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
/** A started game, seat 1 first, built from `game_created`. */
const game = (o: Opts = {}): GameState => run(lobbyOf(o), started(1));
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
const tallies = (state: GameState, target: number): Record<string, number> | undefined =>
  state.commander_damage[String(target)];
const inPhase = (state: GameState, phase: Phase): GameState => ({
  ...state,
  phase,
  resume_phase: phase === 'abandoned' ? 'active' : null,
});
const withSeat = (state: GameState, id: number, changes: Record<string, unknown>): GameState => ({
  ...state,
  seats: state.seats.map((s) => (s.seat === id ? ({ ...s, ...changes } as Seat) : s)),
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

const SECTION_46 = section('### 4.6 ');
const ORDER_TEXT_46 = SECTION_46.slice(
  SECTION_46.indexOf('**Order of the checks.**'),
  SECTION_46.indexOf('Why check 5 comes before check 6'),
);
const ELIMINATION_TEXT_46 = SECTION_46.slice(
  SECTION_46.indexOf('**Elimination and the precedence of reasons.**'),
  SECTION_46.indexOf('Tallies of different source keys'),
);
const CHECKS_46 = numberedCodes(ORDER_TEXT_46);
const ROWS_46 = tableRows(SECTION_46);

// ---------------------------------------------------------------------------------------------
describe('the contract text this file relies on', () => {
  it('4.6 lists seven checks: shape, phase, unknown_seat, seat_eliminated, amount, commander, state numbers', () => {
    expect(CHECKS_46).toEqual([
      'invalid_payload',
      'invalid_phase',
      'unknown_seat',
      'seat_eliminated',
      'invalid_amount',
      'unknown_commander',
      'invalid_amount',
    ]);
  });

  it('4.6 starts its check list with the order of 4.5.1', () => {
    const shared = numberedCodes(section('#### 4.5.1 '));
    expect(CHECKS_46.slice(0, shared.length)).toEqual(shared);
  });

  it('4.6 names the two reasons the event can give, commander_damage first, then life', () => {
    const reasons = [...ELIMINATION_TEXT_46.matchAll(/^\d+\. .*?reason `([a-z_]+)`/gm)].map(
      (m) => m[1]!,
    );
    expect(reasons).toEqual(['commander_damage', 'life']);
    for (const r of reasons) expect(ELIMINATION_REASONS as readonly string[]).toContain(r);
  });

  it('4.5.2 gives the precedence commander_damage, poison, life, and 4.6 uses it without poison', () => {
    const bullet = section('#### 4.5.2 ')
      .split('\n')
      .find((l) => l.startsWith('- **Reason precedence'))!;
    const tail = bullet.slice(bullet.indexOf('in the order') + 'in the order'.length);
    const order = [...tail.slice(0, tail.indexOf('.')).matchAll(/`([a-z_]+)`/g)].map((m) => m[1]!);
    expect(order).toEqual(['commander_damage', 'poison', 'life']);
    const reasons = [...ELIMINATION_TEXT_46.matchAll(/^\d+\. .*?reason `([a-z_]+)`/gm)].map(
      (m) => m[1]!,
    );
    expect(order.filter((r) => reasons.includes(r))).toEqual(reasons);
  });

  it('3.2 gives commander_damage_threshold the range 1 to 99 and the default 21', () => {
    const row = section('### 3.2 ')
      .split('\n')
      .find((l) => l.startsWith('| `commander_damage_threshold`'))!
      .split('|')
      .map((c) => c.trim());
    expect([row[3], row[4]]).toEqual(['1 to 99', '21']);
    expect(lobbyState().settings.commander_damage_threshold).toBe(21);
  });

  it('4.6 has the nineteen example rows these tests are built from', () => {
    expect(ROWS_46.map((r) => Number(r[0]))).toEqual(Array.from({ length: 19 }, (_, i) => i + 1));
  });

  it('the registry row of commander_damage has the four payload keys of the story', () => {
    const row = contract
      .split('\n')
      .find((l) => l.startsWith('| `commander_damage`') && l.includes('`source_seat`'))!;
    expect(row).toContain('`source_seat`, `commander` (index), `target_seat`, `amount`');
  });
});

// ---------------------------------------------------------------------------------------------
describe('criterion 1: one commander, three hits of 7 eliminate (tally 21, life 19)', () => {
  const hit = () => cd(1, 0, 3, 7);

  it('seat 1 has one commander: the unnamed default, index 0', () => {
    expect(seatOf(game(), 1).commanders).toEqual(SOLO);
  });

  it('after the first hit the tally is 7 and the life 33, in the game', () => {
    const state = run(game(), hit());
    expect(tallies(state, 3)).toEqual({ '1:0': 7 });
    expect(seatOf(state, 3)).toMatchObject({ life: 33, eliminated_at_seq: null });
  });

  it('after the second hit the tally is 14 and the life 26, in the game', () => {
    const state = run(game(), hit(), hit());
    expect(tallies(state, 3)).toEqual({ '1:0': 14 });
    expect(seatOf(state, 3)).toMatchObject({ life: 26, eliminated_at_seq: null });
  });

  it('after the third hit the tally is 21 and the life 19, eliminated by commander_damage, eliminated_by 1', () => {
    const before = run(game(), hit(), hit());
    const after = run(before, hit());
    expect(after.commander_damage).toEqual({ '3': { '1:0': 21 } });
    expect(seatOf(after, 3)).toMatchObject({
      life: 19,
      elimination_reason: 'commander_damage',
      eliminated_by: 1,
      eliminated_at_seq: after.last_seq,
      eliminated_round: 1,
      elimination_cause: null,
    });
    expect(after.last_seq).toBe(before.last_seq + 1);
  });

  it('the same numbers from the default settings of initialState, without a game_created', () => {
    const after = run(rawGame({}), hit(), hit(), hit());
    expect(tallies(after, 3)).toEqual({ '1:0': 21 });
    expect(seatOf(after, 3)).toMatchObject({
      life: 19,
      elimination_reason: 'commander_damage',
      eliminated_by: 1,
    });
  });

  it('eliminated_at_seq is the seq the event carries when the server assigned it', () => {
    const before = run(game(), hit(), hit());
    const seq = before.last_seq + 1;
    const after = run(before, make('commander_damage', hit().payload, { seq }));
    expect(seatOf(after, 3).eliminated_at_seq).toBe(seq);
  });

  it('the other seats and their lives are untouched, and nobody has won yet', () => {
    const before = game();
    const after = run(before, hit(), hit(), hit());
    for (const id of [1, 2, 4]) expect(seatOf(after, id)).toEqual(seatOf(before, id));
    expect(inGame(after)).toEqual([1, 2, 4]);
    expect([after.no_winner, after.winner_seat, after.phase]).toEqual([false, null, 'active']);
  });

  it('the elimination does not move the active seat, the turn or the round', () => {
    const before = game();
    const after = run(before, hit(), hit(), hit());
    expect([after.active_seat, after.turn, after.round]).toEqual([
      before.active_seat,
      before.turn,
      before.round,
    ]);
    expect([after.monarch, after.initiative, after.win_condition]).toEqual([null, null, null]);
  });

  it('a fourth hit on the eliminated seat is seat_eliminated', () => {
    const state = run(game(), hit(), hit(), hit());
    expect(codeOf(state, hit())).toBe('seat_eliminated');
  });
});

// ---------------------------------------------------------------------------------------------
describe('criterion 2: partner commanders are tracked as two sources', () => {
  const assignments: Array<[string, Opts]> = [
    ['commanders_set', { partners: [2] }],
    ['deck_assigned', { decks: [2] }],
  ];

  it.each(assignments)(
    'seat 2 has partners from %s: A (2:0) and B (2:1) deal 12 each to seat 4, two tallies of 12, 16 life, not eliminated',
    (_how, opts) => {
      const before = game(opts);
      expect(seatOf(before, 2).commanders).toEqual(PARTNERS);
      expect(seatOf(before, 2).commander_cast).toEqual([0, 0]);
      const after = run(before, cd(2, 0, 4, 12), cd(2, 1, 4, 12));
      expect(after.commander_damage).toEqual({ '4': { '2:0': 12, '2:1': 12 } });
      expect(seatOf(after, 4)).toMatchObject({
        life: 16,
        elimination_reason: null,
        eliminated_by: null,
        eliminated_at_seq: null,
      });
      expect(inGame(after)).toEqual([1, 2, 3, 4]);
      expect(after.no_winner).toBe(false);
    },
  );

  it('the order of the two hits does not matter', () => {
    const base = game({ partners: [2] });
    const ab = run(base, cd(2, 0, 4, 12), cd(2, 1, 4, 12));
    const ba = run(base, cd(2, 1, 4, 12), cd(2, 0, 4, 12));
    expect(canon(ab)).toBe(canon(ba));
  });

  it('24 from one source in two tallies of 12 never adds up to the threshold of 21', () => {
    const after = run(game({ partners: [2] }), cd(2, 0, 4, 12), cd(2, 1, 4, 12));
    expect(Object.values(tallies(after, 4)!).reduce((a, b) => a + b, 0)).toBe(24);
    expect(seatOf(after, 4).eliminated_at_seq).toBeNull();
  });

  it('9 more from A makes 21 from one commander: eliminated by commander_damage, eliminated_by 2 (4.6 row 3)', () => {
    const after = run(game({ partners: [2] }), cd(2, 0, 4, 12), cd(2, 1, 4, 12), cd(2, 0, 4, 9));
    expect(tallies(after, 4)).toEqual({ '2:0': 21, '2:1': 12 });
    expect(seatOf(after, 4)).toMatchObject({
      life: 7,
      elimination_reason: 'commander_damage',
      eliminated_by: 2,
    });
  });

  it('partner B alone can reach the threshold: 2:1 at 21 eliminates, 2:0 stays 0 (not created)', () => {
    const after = run(game({ partners: [2] }), cd(2, 1, 4, 21));
    expect(tallies(after, 4)).toEqual({ '2:1': 21 });
    expect(seatOf(after, 4)).toMatchObject({
      elimination_reason: 'commander_damage',
      eliminated_by: 2,
    });
  });

  it('a seat with one commander and a seat with two share a table: the keys are told apart by seat', () => {
    const after = run(game({ partners: [2] }), cd(1, 0, 4, 3), cd(2, 0, 4, 4), cd(2, 1, 4, 5));
    expect(tallies(after, 4)).toEqual({ '1:0': 3, '2:0': 4, '2:1': 5 });
    expect(seatOf(after, 4).life).toBe(28);
  });
});

// ---------------------------------------------------------------------------------------------
describe('criterion 3: the tally and the life change in one event', () => {
  /** The state a hit must give, from the words of 4.6: only these fields change. */
  function expectedAfter(
    before: GameState,
    [s, c, t, a]: [number, number, number, number],
    elimination?: { reason: string },
  ): Plain {
    const p = plain(before);
    const row = (p.commander_damage[String(t)] ??= {});
    row[`${s}:${c}`] = (row[`${s}:${c}`] ?? 0) + a;
    const target = p.seats.find((x) => x.seat === t)!;
    target.life = (target.life as number) - a;
    p.last_seq += 1;
    if (elimination) {
      target.elimination_reason = elimination.reason;
      target.eliminated_by = s;
      target.eliminated_at_seq = p.last_seq;
      target.eliminated_round = p.round;
    }
    return p;
  }

  it('one hit changes the tally, the life and last_seq, and nothing else in the state', () => {
    const before = game({ partners: [2] });
    const after = run(before, cd(2, 1, 4, 7));
    expect(canon(after)).toBe(canonicalJson(expectedAfter(before, [2, 1, 4, 7])));
  });

  it('the hit that eliminates also changes only the tally, the life, the five elimination fields and last_seq', () => {
    const before = run(game(), cd(1, 0, 3, 7), cd(1, 0, 3, 7));
    const after = run(before, cd(1, 0, 3, 7));
    expect(canon(after)).toBe(
      canonicalJson(expectedAfter(before, [1, 0, 3, 7], { reason: 'commander_damage' })),
    );
  });

  it('a second hit on a row that exists adds to the key and leaves the other keys alone', () => {
    const before = run(game({ partners: [2] }), cd(2, 0, 4, 5), cd(1, 0, 4, 2));
    const after = run(before, cd(2, 0, 4, 6));
    expect(canon(after)).toBe(canonicalJson(expectedAfter(before, [2, 0, 4, 6])));
    expect(tallies(after, 4)).toEqual({ '2:0': 11, '1:0': 2 });
  });

  const HITS: Array<[number, number, number]> = [
    [1, 0, 3],
    [1, 0, 3],
    [1, 0, 3],
  ];
  const AMOUNTS = [7, 5, 9];

  function log(skip: number | null): EventEnvelope[] {
    return [
      ...lobbyLog(),
      started(1),
      ...HITS.map(([s, c, t], i) => cd(s, c, t, AMOUNTS[i]!)).filter((_, i) => i !== skip),
    ];
  }

  it('a state rebuilt from the log without the last hit equals the state before it, tally and life together', () => {
    const beforeTheHit = run(game(), cd(1, 0, 3, 7), cd(1, 0, 3, 5));
    const full = replay(lobbyState(), log(null));
    const without = replay(lobbyState(), log(2));
    expect(full.ok && without.ok).toBe(true);
    if (!full.ok || !without.ok) return;
    expect(canon(without.state)).toBe(canon(beforeTheHit));
    expect(seatOf(without.state, 3)).toMatchObject({ life: 28, eliminated_at_seq: null });
    expect(tallies(without.state, 3)).toEqual({ '1:0': 12 });
    expect(seatOf(full.state, 3).eliminated_at_seq).not.toBeNull();
  });

  it.each([0, 1, 2])(
    'a log without hit %i has tally = sum of the remaining hits and life = 40 - that sum',
    (skip) => {
      const rebuilt = replay(lobbyState(), log(skip));
      expect(rebuilt.ok).toBe(true);
      if (!rebuilt.ok) return;
      const sum = AMOUNTS.filter((_, i) => i !== skip).reduce((a, b) => a + b, 0);
      expect(tallies(rebuilt.state, 3)).toEqual({ '1:0': sum });
      expect(seatOf(rebuilt.state, 3)).toMatchObject({ life: 40 - sum, eliminated_at_seq: null });
    },
  );

  it('the tally minus the life stays the same in every state of the log: they move together', () => {
    let state = game();
    for (const amount of [7, 5, 9]) {
      state = run(state, cd(1, 0, 3, amount));
      const total = Object.values(tallies(state, 3)!).reduce((a, b) => a + b, 0);
      expect(40 - seatOf(state, 3).life).toBe(total);
    }
  });
});

// ---------------------------------------------------------------------------------------------
describe('criterion 4: the reason is commander_damage > poison > life', () => {
  it('tally 21 and life 0 in the same event: commander_damage, eliminated_by 1 (4.6 row 4)', () => {
    const before = run(game(), lc(4, -19));
    expect(seatOf(before, 4).life).toBe(21);
    const after = run(before, cd(1, 0, 4, 21));
    expect(tallies(after, 4)).toEqual({ '1:0': 21 });
    expect(seatOf(after, 4)).toMatchObject({
      life: 0,
      elimination_reason: 'commander_damage',
      eliminated_by: 1,
      eliminated_at_seq: after.last_seq,
    });
  });

  it('tally 25 and life -15 in the same event: commander_damage, life not clamped (4.6 row 5)', () => {
    const after = run(game(), lc(4, -30), cd(1, 0, 4, 25));
    expect(tallies(after, 4)).toEqual({ '1:0': 25 });
    expect(seatOf(after, 4)).toMatchObject({
      life: -15,
      elimination_reason: 'commander_damage',
      eliminated_by: 1,
    });
  });

  it('life 0 with a tally of 5: the reason is life, eliminated_by 1 (4.6 row 6)', () => {
    const after = run(game(), lc(2, -35), cd(1, 0, 2, 5));
    expect(tallies(after, 2)).toEqual({ '1:0': 5 });
    expect(seatOf(after, 2)).toMatchObject({
      life: 0,
      elimination_reason: 'life',
      eliminated_by: 1,
      eliminated_at_seq: after.last_seq,
      elimination_cause: null,
    });
  });

  it('life -15 with a tally of 20 (one below 21): the reason is life (4.6 row 7)', () => {
    const after = run(game(), lc(2, -35), cd(1, 0, 2, 20));
    expect(tallies(after, 2)).toEqual({ '1:0': 20 });
    expect(seatOf(after, 2)).toMatchObject({
      life: -15,
      elimination_reason: 'life',
      eliminated_by: 1,
    });
  });

  it('a tally at the threshold with life still above 0: commander_damage', () => {
    const after = run(game(), cd(1, 0, 3, 21));
    expect(seatOf(after, 3)).toMatchObject({ life: 19, elimination_reason: 'commander_damage' });
  });

  it('life 1 and a tally below the threshold: no elimination', () => {
    const after = run(game(), lc(2, -35), cd(1, 0, 2, 4));
    expect(seatOf(after, 2)).toMatchObject({ life: 1, eliminated_at_seq: null });
  });

  it('two partners at 12 and 12 are not 24: both tallies stay, no elimination', () => {
    const after = run(game({ partners: [2] }), cd(2, 0, 4, 12), cd(2, 1, 4, 12));
    expect(seatOf(after, 4).eliminated_at_seq).toBeNull();
  });

  it('two source seats at 12 and 12 are not 24: no elimination', () => {
    const after = run(game(), cd(1, 0, 2, 12), cd(3, 0, 2, 12));
    expect(tallies(after, 2)).toEqual({ '1:0': 12, '3:0': 12 });
    expect(seatOf(after, 2)).toMatchObject({ life: 16, eliminated_at_seq: null });
  });

  it('two sources at 20 each take 40 life and no tally reaches 21: the reason is life, eliminated_by the second source', () => {
    const after = run(game(), cd(1, 0, 2, 20), cd(3, 0, 2, 20));
    expect(tallies(after, 2)).toEqual({ '1:0': 20, '3:0': 20 });
    expect(seatOf(after, 2)).toMatchObject({
      life: 0,
      elimination_reason: 'life',
      eliminated_by: 3,
      eliminated_at_seq: after.last_seq,
    });
  });

  it('three sources at 20 each with 999 starting life stay in the game: no tally reaches 21', () => {
    const after = run(game({ life: 999 }), cd(1, 0, 2, 20), cd(3, 0, 2, 20), cd(4, 0, 2, 20));
    expect(tallies(after, 2)).toEqual({ '1:0': 20, '3:0': 20, '4:0': 20 });
    expect(seatOf(after, 2)).toMatchObject({ life: 939, eliminated_at_seq: null });
  });

  it('the commander damage event never makes poison the reason: a seat with poison above the threshold is not eliminated by it', () => {
    const before = withSeat(game(), 2, { poison: 12 });
    const after = run(before, cd(1, 0, 2, 1));
    expect(seatOf(after, 2)).toMatchObject({ poison: 12, life: 39, eliminated_at_seq: null });
  });

  it('a seat eliminated by a tally of 25 takes no further hit', () => {
    const state = run(game(), cd(1, 0, 3, 25));
    expect(codeOf(state, cd(1, 0, 3, 1))).toBe('seat_eliminated');
  });

  it('a hit that takes the life to 0 or less but whose tally is below the threshold is a life elimination, in ending too', () => {
    const state = inPhase(run(game(), lc(2, -35)), 'ending');
    const after = run(state, cd(3, 0, 2, 5));
    expect(seatOf(after, 2)).toMatchObject({ elimination_reason: 'life', eliminated_by: 3 });
  });
});

// ---------------------------------------------------------------------------------------------
describe('criterion 5: invalid_amount and unknown_commander', () => {
  it.each([0, -1, -999, -1000, 1000, 1001, 1000000, MAX_SAFE, -MAX_SAFE])(
    'amount %i is invalid_amount',
    (amount) => {
      expect(codeOf(game(), cd(1, 0, 2, amount))).toBe('invalid_amount');
    },
  );

  it.each([1, 2, 998, 999])('amount %i is accepted', (amount) => {
    expect(codeOf(game({ life: 999 }), cd(1, 0, 2, amount))).toBe('ok');
  });

  it('amount 1 and amount 999 give the exact tally and life', () => {
    const low = run(game(), cd(1, 0, 2, 1));
    expect([tallies(low, 2), seatOf(low, 2).life]).toEqual([{ '1:0': 1 }, 39]);
    const high = run(game({ life: 999 }), cd(1, 0, 2, 999));
    expect([tallies(high, 2), seatOf(high, 2).life]).toEqual([{ '1:0': 999 }, 0]);
    expect(seatOf(high, 2).elimination_reason).toBe('commander_damage');
  });

  it.each(['1', true, false, null, [], {}, 1.5, -0.5])(
    'amount %j is of the wrong type: invalid_payload, not invalid_amount',
    (amount) => {
      expect(codeOf(game(), cd(1, 0, 2, amount))).toBe('invalid_payload');
    },
  );

  it('an amount outside the safe range is invalid_payload (6.2 step 5)', () => {
    expect(codeOf(game(), cd(1, 0, 2, MAX_SAFE + 1))).toBe('invalid_payload');
    expect(codeOf(game(), cd(1, 0, 2, -MAX_SAFE - 1))).toBe('invalid_payload');
  });

  it('a missing amount is invalid_payload', () => {
    expect(
      codeOf(game(), make('commander_damage', { source_seat: 1, commander: 0, target_seat: 2 })),
    ).toBe('invalid_payload');
  });

  it.each([1, 2, -1, 5, MAX_SAFE])(
    'commander %i of a seat with one commander is unknown_commander',
    (commander) => {
      expect(codeOf(game(), cd(1, commander, 2, 1))).toBe('unknown_commander');
    },
  );

  it.each([2, 3, -1, MAX_SAFE])(
    'commander %i of a seat with partners is unknown_commander',
    (commander) => {
      expect(codeOf(game({ partners: [2] }), cd(2, commander, 3, 1))).toBe('unknown_commander');
    },
  );

  it.each([0, 1])('commander %i of a seat with partners is accepted', (commander) => {
    expect(codeOf(game({ partners: [2] }), cd(2, commander, 3, 1))).toBe('ok');
  });

  it('the index is checked against the source seat, not the target: a single-commander source hitting a partner seat', () => {
    expect(codeOf(game({ partners: [3] }), cd(1, 1, 3, 1))).toBe('unknown_commander');
  });

  it('the index is checked against the source seat, not the target: a partner source hitting a single-commander seat', () => {
    expect(codeOf(game({ partners: [2] }), cd(2, 1, 3, 1))).toBe('ok');
  });

  it('a seat that was never given a commander has the unnamed default, so index 0 only', () => {
    const state = game();
    expect(codeOf(state, cd(2, 0, 3, 1))).toBe('ok');
    expect(codeOf(state, cd(2, 1, 3, 1))).toBe('unknown_commander');
  });

  it('commanders replaced by one commander in the lobby leave only index 0', () => {
    const lobby = run(
      lobbyOf({ partners: [2] }),
      ev('commanders_set', { seat: 2, commanders: SOLO }),
    );
    const state = run(lobby, started(1));
    expect(codeOf(state, cd(2, 1, 3, 1))).toBe('unknown_commander');
    expect(codeOf(state, cd(2, 0, 3, 1))).toBe('ok');
  });

  it.each(['1', true, null, [], {}, 0.5])(
    'commander %j is of the wrong type: invalid_payload, not unknown_commander',
    (commander) => {
      expect(codeOf(game(), cd(1, commander, 2, 1))).toBe('invalid_payload');
    },
  );

  it('a missing commander is invalid_payload', () => {
    expect(
      codeOf(game(), make('commander_damage', { source_seat: 1, target_seat: 2, amount: 1 })),
    ).toBe('invalid_payload');
  });

  it('a rejected event changes nothing: the state is the same bytes', () => {
    const state = run(game({ partners: [2] }), cd(2, 0, 3, 4));
    const snapshot = canon(state);
    for (const e of [cd(1, 1, 3, 1), cd(1, 0, 3, 0), cd(1, 0, 3, 1000), cd(1, 0, 7, 1)]) {
      expect(apply(state, e).ok).toBe(false);
      expect(canon(state)).toBe(snapshot);
    }
  });

  it('an amount error is reported before a commander error (both wrong)', () => {
    expect(codeOf(game(), cd(1, 5, 2, 1000))).toBe('invalid_amount');
    expect(codeOf(game(), cd(1, 5, 2, 0))).toBe('invalid_amount');
  });

  it.each([0, 9, -1, '1', null])('a source seat of %j is invalid_payload', (source) => {
    expect(codeOf(game(), cd(source, 0, 2, 1))).toBe('invalid_payload');
  });

  it('a source seat or a target seat that is not at the table is unknown_seat', () => {
    expect(codeOf(game({ count: 3 }), cd(4, 0, 2, 1))).toBe('unknown_seat');
    expect(codeOf(game({ count: 3 }), cd(1, 0, 4, 1))).toBe('unknown_seat');
    expect(codeOf(game({ count: 3 }), cd(5, 0, 4, 1))).toBe('unknown_seat');
  });
});

// ---------------------------------------------------------------------------------------------
describe('criterion 6: commander_damage_threshold', () => {
  const settingsWith = (value: unknown) => ({
    starting_life: 40,
    commander_damage_threshold: value,
    poison_threshold: 10,
    trust_mode: 'casual',
  });

  it('the default is 21: a tally of 20 does not eliminate and 21 does', () => {
    const state = lobbyState();
    expect(state.settings.commander_damage_threshold).toBe(21);
    const twenty = run(game(), cd(1, 0, 2, 7), cd(1, 0, 2, 7), cd(1, 0, 2, 6));
    expect(tallies(twenty, 2)).toEqual({ '1:0': 20 });
    expect(seatOf(twenty, 2).eliminated_at_seq).toBeNull();
    const twentyOne = run(twenty, cd(1, 0, 2, 1));
    expect(seatOf(twentyOne, 2)).toMatchObject({
      elimination_reason: 'commander_damage',
      eliminated_by: 1,
    });
  });

  it('game_created with the default threshold 21 behaves as the default', () => {
    const after = run(lobbyState(), ...lobbyLog(), started(1), cd(1, 0, 2, 21));
    expect(after.settings.commander_damage_threshold).toBe(21);
    expect(seatOf(after, 2).elimination_reason).toBe('commander_damage');
  });

  it('threshold 15 from game_created: a tally of 14 stays, 15 eliminates, life 25', () => {
    const state = game({ threshold: 15 });
    expect(state.settings.commander_damage_threshold).toBe(15);
    const fourteen = run(state, cd(1, 0, 2, 7), cd(1, 0, 2, 7));
    expect(seatOf(fourteen, 2)).toMatchObject({ life: 26, eliminated_at_seq: null });
    const after = run(state, cd(1, 0, 2, 7), cd(1, 0, 2, 8));
    expect(tallies(after, 2)).toEqual({ '1:0': 15 });
    expect(seatOf(after, 2)).toMatchObject({
      life: 25,
      elimination_reason: 'commander_damage',
      eliminated_by: 1,
      eliminated_at_seq: after.last_seq,
    });
  });

  it('threshold 15 from settings_changed in the lobby gives the same result', () => {
    const lobby = run(lobbyOf(), ev('settings_changed', { commander_damage_threshold: 15 }));
    const after = run(lobby, started(1), cd(1, 0, 2, 7), cd(1, 0, 2, 8));
    expect(seatOf(after, 2)).toMatchObject({
      life: 25,
      elimination_reason: 'commander_damage',
      eliminated_by: 1,
    });
  });

  it('threshold 15 from the settings of initialState gives the same result', () => {
    const after = run(rawGame({ commander_damage_threshold: 15 }), cd(1, 0, 2, 7), cd(1, 0, 2, 8));
    expect(seatOf(after, 2).elimination_reason).toBe('commander_damage');
  });

  it('with threshold 15, 7 from one commander and 8 from another is 15 in total but no elimination', () => {
    const after = run(game({ threshold: 15 }), cd(1, 0, 2, 7), cd(3, 0, 2, 8));
    expect(seatOf(after, 2)).toMatchObject({ life: 25, eliminated_at_seq: null });
  });

  it('with threshold 15, partners at 14 and 14 stay in the game and 15 from one of them eliminates', () => {
    const state = run(game({ threshold: 15, partners: [2] }), cd(2, 0, 4, 14), cd(2, 1, 4, 14));
    expect(seatOf(state, 4)).toMatchObject({ life: 12, eliminated_at_seq: null });
    const after = run(state, cd(2, 1, 4, 1));
    expect(tallies(after, 4)).toEqual({ '2:0': 14, '2:1': 15 });
    expect(seatOf(after, 4)).toMatchObject({
      elimination_reason: 'commander_damage',
      eliminated_by: 2,
    });
  });

  it('with threshold 15, a single hit of 20 eliminates (tally 20 is at least 15)', () => {
    const after = run(game({ threshold: 15 }), cd(1, 0, 2, 20));
    expect(seatOf(after, 2)).toMatchObject({ life: 20, elimination_reason: 'commander_damage' });
  });

  it('threshold 1: the first point eliminates, life 39', () => {
    const after = run(game({ threshold: 1 }), cd(1, 0, 2, 1));
    expect(seatOf(after, 2)).toMatchObject({
      life: 39,
      elimination_reason: 'commander_damage',
      eliminated_by: 1,
    });
  });

  it('threshold 99: 98 stays in the game, one more eliminates (starting life 999)', () => {
    const state = game({ threshold: 99, life: 999 });
    const held = run(state, cd(1, 0, 2, 98));
    expect(seatOf(held, 2)).toMatchObject({ life: 901, eliminated_at_seq: null });
    const after = run(held, cd(1, 0, 2, 1));
    expect(tallies(after, 2)).toEqual({ '1:0': 99 });
    expect(seatOf(after, 2)).toMatchObject({ life: 900, elimination_reason: 'commander_damage' });
  });

  it.each([1, 15, 21, 99])('game_created and settings_changed accept the threshold %i', (value) => {
    expect(codeOf(lobbyState(), ev('game_created', { settings: settingsWith(value) }))).toBe('ok');
    expect(
      codeOf(lobbyState(), ev('settings_changed', { commander_damage_threshold: value })),
    ).toBe('ok');
  });

  it.each([0, 100, -1, 101, 1000, MAX_SAFE, '21', true, null])(
    'the threshold %j is invalid_setting in game_created and in settings_changed',
    (value) => {
      expect(codeOf(lobbyState(), ev('game_created', { settings: settingsWith(value) }))).toBe(
        'invalid_setting',
      );
      expect(
        codeOf(lobbyState(), ev('settings_changed', { commander_damage_threshold: value })),
      ).toBe('invalid_setting');
    },
  );

  it('a rejected threshold leaves the settings as they were', () => {
    const lobby = lobbyOf({ threshold: 15 });
    expect(codeOf(lobby, ev('settings_changed', { commander_damage_threshold: 100 }))).toBe(
      'invalid_setting',
    );
    expect(lobby.settings.commander_damage_threshold).toBe(15);
  });

  it('the threshold cannot change once the game started (settings_changed is invalid_phase)', () => {
    expect(codeOf(game(), ev('settings_changed', { commander_damage_threshold: 15 }))).toBe(
      'invalid_phase',
    );
  });

  it('an integer threshold outside 1 to 99 in an unvalidated state is used as given (4.6)', () => {
    const zero = run(rawGame({ commander_damage_threshold: 0 }), cd(1, 0, 2, 1));
    expect(seatOf(zero, 2).elimination_reason).toBe('commander_damage');
    const huge = run(
      rawGame({ commander_damage_threshold: 100, starting_life: 999 }),
      cd(1, 0, 2, 99),
    );
    expect(seatOf(huge, 2).eliminated_at_seq).toBeNull();
  });
});

// ---------------------------------------------------------------------------------------------
describe('phases: lobby, finished and abandoned reject; active and ending accept', () => {
  const hit = () => cd(1, 0, 2, 5);

  it('the lobby is invalid_phase, and a lobby event changes nothing', () => {
    const lobby = lobbyOf();
    expect(apply(lobby, hit())).toEqual({ ok: false, error: { code: 'invalid_phase' } });
    expect(lobby.commander_damage).toEqual({});
    expect(lobby.seats.map((s) => s.life)).toEqual([40, 40, 40, 40]);
  });

  it.each(['finished', 'abandoned'] as const)('%s is invalid_phase', (phase) => {
    expect(codeOf(inPhase(game(), phase), hit())).toBe('invalid_phase');
  });

  it('active accepts', () => {
    expect(codeOf(game(), hit())).toBe('ok');
  });

  describe('ending (hand-built)', () => {
    const ending = () => inPhase(game(), 'ending');

    it('accepts and stays in ending, with the same tally and life as in active', () => {
      const after = run(ending(), hit());
      expect(after.phase).toBe('ending');
      expect(tallies(after, 2)).toEqual({ '1:0': 5 });
      expect(seatOf(after, 2).life).toBe(35);
    });

    it('an elimination by commander damage in ending records the reason, eliminator and seq', () => {
      const before = ending();
      const after = run(before, cd(1, 0, 2, 21));
      expect(seatOf(after, 2)).toMatchObject({
        life: 19,
        elimination_reason: 'commander_damage',
        eliminated_by: 1,
        eliminated_at_seq: before.last_seq + 1,
      });
      expect(after.phase).toBe('ending');
    });

    it('the other rules hold in ending', () => {
      expect(codeOf(ending(), cd(1, 0, 7, 1))).toBe('unknown_seat');
      expect(codeOf(ending(), cd(1, 0, 2, 0))).toBe('invalid_amount');
      expect(codeOf(ending(), cd(1, 1, 2, 1))).toBe('unknown_commander');
      expect(codeOf(ending(), cd(1, 0, 2, '1'))).toBe('invalid_payload');
      expect(codeOf(run(ending(), concede(2)), cd(1, 0, 2, 1))).toBe('seat_eliminated');
    });
  });
});

// ---------------------------------------------------------------------------------------------
describe('an eliminated source or target (seat_eliminated), and self damage', () => {
  it('an eliminated target is seat_eliminated, however it was eliminated', () => {
    expect(codeOf(run(game(), concede(2)), cd(1, 0, 2, 1))).toBe('seat_eliminated');
    expect(codeOf(run(game(), lc(2, -40)), cd(1, 0, 2, 1))).toBe('seat_eliminated');
    expect(codeOf(run(game(), cd(3, 0, 2, 21)), cd(1, 0, 2, 1))).toBe('seat_eliminated');
    expect(codeOf(outAt(game(), 2, 5), cd(1, 0, 2, 1))).toBe('seat_eliminated');
  });

  it('an eliminated source is seat_eliminated, however it was eliminated', () => {
    expect(codeOf(run(game(), concede(2)), cd(2, 0, 1, 1))).toBe('seat_eliminated');
    expect(codeOf(run(game(), lc(2, -40)), cd(2, 0, 1, 1))).toBe('seat_eliminated');
    expect(codeOf(run(game(), cd(3, 0, 2, 21)), cd(2, 0, 1, 1))).toBe('seat_eliminated');
  });

  it('an eliminated source with a partner index it has is also seat_eliminated', () => {
    const state = run(game({ partners: [2] }), concede(2));
    expect(codeOf(state, cd(2, 1, 1, 1))).toBe('seat_eliminated');
  });

  it('seat_eliminated comes before the amount and the commander checks, for the source and the target', () => {
    const state = run(game(), concede(2));
    expect(codeOf(state, cd(1, 0, 2, 0))).toBe('seat_eliminated');
    expect(codeOf(state, cd(1, 5, 2, 1))).toBe('seat_eliminated');
    expect(codeOf(state, cd(2, 0, 1, 1000))).toBe('seat_eliminated');
    expect(codeOf(state, cd(2, 5, 1, 1))).toBe('seat_eliminated');
    expect(codeOf(state, cd(2, 5, 2, 1000))).toBe('seat_eliminated');
  });

  it('unknown_seat comes before seat_eliminated: the source is checked first, then the target', () => {
    const state = run(game(), concede(2));
    expect(codeOf(state, cd(1, 0, 7, 1))).toBe('unknown_seat');
    expect(codeOf(state, cd(7, 0, 2, 1))).toBe('unknown_seat');
  });

  it('a rejected event on an eliminated seat changes nothing', () => {
    const state = run(game(), concede(2));
    const snapshot = canon(state);
    apply(state, cd(1, 0, 2, 5));
    expect(canon(state)).toBe(snapshot);
  });

  it('the tallies of a seat that was eliminated are kept, and so are the tallies it dealt', () => {
    const after = run(game(), cd(3, 0, 2, 10), concede(3), cd(1, 0, 2, 4));
    expect(tallies(after, 2)).toEqual({ '3:0': 10, '1:0': 4 });
    const dealt = run(game(), cd(1, 0, 2, 21));
    expect(tallies(dealt, 2)).toEqual({ '1:0': 21 });
    expect(seatOf(dealt, 2).eliminated_at_seq).not.toBeNull();
  });

  it('self damage is legal: seat 2 hits itself with 5 from 2:0, tally 5 life 35', () => {
    const after = run(game(), cd(2, 0, 2, 5));
    expect(after.commander_damage).toEqual({ '2': { '2:0': 5 } });
    expect(seatOf(after, 2)).toMatchObject({ life: 35, eliminated_at_seq: null });
  });

  it('self damage that reaches 21 eliminates with eliminated_by the seat itself', () => {
    const after = run(game(), cd(2, 0, 2, 21));
    expect(seatOf(after, 2)).toMatchObject({
      life: 19,
      elimination_reason: 'commander_damage',
      eliminated_by: 2,
    });
  });

  it('self damage by a partner uses the partner key', () => {
    const after = run(game({ partners: [2] }), cd(2, 1, 2, 21));
    expect(after.commander_damage).toEqual({ '2': { '2:1': 21 } });
    expect(seatOf(after, 2).eliminated_by).toBe(2);
  });

  it('two seats left: eliminating one leaves the other, no_winner stays false and nobody is named the winner', () => {
    const after = run(game({ count: 2 }), cd(1, 0, 2, 21));
    expect(inGame(after)).toEqual([1]);
    expect([after.no_winner, after.winner_seat, after.phase]).toEqual([false, null, 'active']);
  });

  it('the last seat in the game eliminating itself is a full draw (the literal rule of 4.5.2)', () => {
    const after = run(game({ count: 2 }), concede(1), cd(2, 0, 2, 21));
    expect(after.no_winner).toBe(true);
    expect(after.seats.map((s) => s.eliminated_at_seq)).toEqual([5, 6]);
    expect(after.phase).toBe('active');
  });

  it('after a full draw every seat-naming event is seat_eliminated', () => {
    const state = run(game({ count: 2 }), concede(1), cd(2, 0, 2, 21));
    expect(codeOf(state, cd(1, 0, 2, 1))).toBe('seat_eliminated');
    expect(codeOf(state, ev('turn_passed'))).toBe('invalid_phase');
  });
});

// ---------------------------------------------------------------------------------------------
describe('check order (4.6): shape, phase, unknown_seat, seat_eliminated, amount, commander, numbers', () => {
  type Ctx = { state: GameState; payload: Payload };
  // One violation per check of the parsed list, in the same order. They do not interfere.
  const MAKERS: Array<(c: Ctx) => Ctx> = [
    (c) => ({ ...c, payload: { ...c.payload, amount: '5' } }), // invalid_payload
    (c) => ({ ...c, state: inPhase(c.state, 'finished') }), // invalid_phase
    (c) => ({ ...c, payload: { ...c.payload, source_seat: 7 } }), // unknown_seat
    (c) => ({ ...c, state: outAt(c.state, 3, 5) }), // seat_eliminated (the target)
    (c) => ({ ...c, payload: { ...c.payload, amount: 0 } }), // invalid_amount (amount)
    (c) => ({ ...c, payload: { ...c.payload, commander: 1 } }), // unknown_commander
    (c) => ({ ...c, state: withSeat(c.state, 3, { life: '40' }) }), // invalid_amount (numbers)
  ];

  it('the 4.6 list has one violation maker per check', () => {
    expect(MAKERS).toHaveLength(CHECKS_46.length);
  });

  it('every set of simultaneous violations gives the earliest check that fails (127 sets)', () => {
    const failures: string[] = [];
    for (let mask = 1; mask < 1 << CHECKS_46.length; mask++) {
      const picked = CHECKS_46.map((_, i) => i).filter((i) => (mask & (1 << i)) !== 0);
      let ctx: Ctx = {
        state: game(),
        payload: { source_seat: 1, commander: 0, target_seat: 3, amount: 5 },
      };
      // Latest check's violation first, so an earlier one that overwrites the same field wins.
      for (const i of [...picked].reverse()) ctx = MAKERS[i]!(ctx);
      const actual = codeOf(ctx.state, ev('commander_damage', ctx.payload));
      const want = CHECKS_46[picked[0]!]!;
      if (actual !== want) failures.push(`${picked.map((i) => i + 1).join('+')} gave ${actual}`);
    }
    expect(failures).toEqual([]);
  });

  it('each single violation gives its own code', () => {
    CHECKS_46.forEach((code, i) => {
      const ctx = MAKERS[i]!({
        state: game(),
        payload: { source_seat: 1, commander: 0, target_seat: 3, amount: 5 },
      });
      expect(codeOf(ctx.state, ev('commander_damage', ctx.payload))).toBe(code);
    });
  });

  it('a bad payload in a closed game is invalid_payload, and an unknown seat in one is invalid_phase', () => {
    expect(codeOf(inPhase(game(), 'finished'), cd(1, 0, 2, 'x'))).toBe('invalid_payload');
    expect(codeOf(inPhase(game(), 'finished'), cd(1, 0, 7, 1))).toBe('invalid_phase');
  });
});

// ---------------------------------------------------------------------------------------------
describe('4.6 worked examples, row by row (parsed so the table and the code cannot drift)', () => {
  const rawWith = (extra: Record<string, unknown>, ...events: EventEnvelope[]) =>
    run(rawGame(extra), ...events);

  type Spec = {
    /** Numbers the result cell must mention. */
    mentions?: number[];
    /** The code the result cell must name; every event of `bad` must give it. */
    code?: string;
    bad?: Array<[GameState, EventEnvelope]>;
    check?: () => void;
  };
  const row = (n: number): string => ROWS_46[n - 1]![3]!;
  const out = (state: GameState, id: number, reason: string, by: number) =>
    expect(seatOf(state, id)).toMatchObject({ elimination_reason: reason, eliminated_by: by });

  const SPECS: Record<number, Spec> = {
    1: {
      mentions: [7, 14, 21, 33, 26, 19],
      check: () => {
        const s1 = run(game(), cd(1, 0, 3, 7));
        const s2 = run(s1, cd(1, 0, 3, 7));
        const s3 = run(s2, cd(1, 0, 3, 7));
        expect([tallies(s1, 3), tallies(s2, 3), tallies(s3, 3)]).toEqual([
          { '1:0': 7 },
          { '1:0': 14 },
          { '1:0': 21 },
        ]);
        expect([seatOf(s1, 3).life, seatOf(s2, 3).life, seatOf(s3, 3).life]).toEqual([33, 26, 19]);
        expect([seatOf(s1, 3).eliminated_at_seq, seatOf(s2, 3).eliminated_at_seq]).toEqual([
          null,
          null,
        ]);
        out(s3, 3, 'commander_damage', 1);
      },
    },
    2: {
      mentions: [12, 16],
      check: () => {
        const s = run(game({ partners: [2] }), cd(2, 0, 4, 12), cd(2, 1, 4, 12));
        expect(s.commander_damage).toEqual({ '4': { '2:0': 12, '2:1': 12 } });
        expect(seatOf(s, 4)).toMatchObject({ life: 16, eliminated_at_seq: null });
      },
    },
    3: {
      mentions: [21, 12, 7, 2],
      check: () => {
        const s = run(game({ partners: [2] }), cd(2, 0, 4, 12), cd(2, 1, 4, 12), cd(2, 0, 4, 9));
        expect(tallies(s, 4)).toEqual({ '2:0': 21, '2:1': 12 });
        expect(seatOf(s, 4).life).toBe(7);
        out(s, 4, 'commander_damage', 2);
      },
    },
    4: {
      mentions: [21, 0, 1],
      check: () => {
        const s = run(game({ life: 21 }), cd(1, 0, 4, 21));
        expect(tallies(s, 4)).toEqual({ '1:0': 21 });
        expect(seatOf(s, 4).life).toBe(0);
        out(s, 4, 'commander_damage', 1);
      },
    },
    5: {
      mentions: [25, 15],
      check: () => {
        const s = run(game({ life: 10 }), cd(1, 0, 4, 25));
        expect(tallies(s, 4)).toEqual({ '1:0': 25 });
        expect(seatOf(s, 4).life).toBe(-15);
        expect(seatOf(s, 4).elimination_reason).toBe('commander_damage');
      },
    },
    6: {
      mentions: [5, 0, 1],
      check: () => {
        const s = run(game({ life: 5 }), cd(1, 0, 2, 5));
        expect(tallies(s, 2)).toEqual({ '1:0': 5 });
        expect(seatOf(s, 2).life).toBe(0);
        out(s, 2, 'life', 1);
      },
    },
    7: {
      mentions: [20, 21, 15],
      check: () => {
        const s = run(game({ life: 5 }), cd(1, 0, 2, 20));
        expect(tallies(s, 2)).toEqual({ '1:0': 20 });
        expect(seatOf(s, 2).life).toBe(-15);
        expect(seatOf(s, 2).elimination_reason).toBe('life');
      },
    },
    8: {
      mentions: [14, 15, 25],
      check: () => {
        const a = run(game({ threshold: 15 }), cd(1, 0, 2, 7));
        const b = run(a, cd(1, 0, 2, 8));
        const c = run(a, cd(1, 0, 2, 7));
        expect(tallies(c, 2)).toEqual({ '1:0': 14 });
        expect(seatOf(c, 2).eliminated_at_seq).toBeNull();
        expect(tallies(b, 2)).toEqual({ '1:0': 15 });
        expect(seatOf(b, 2).life).toBe(25);
        expect(seatOf(b, 2).elimination_reason).toBe('commander_damage');
      },
    },
    9: {
      mentions: [21, 19, 2],
      check: () => {
        const s = run(game(), cd(2, 0, 2, 21));
        expect(s.commander_damage).toEqual({ '2': { '2:0': 21 } });
        expect(seatOf(s, 2).life).toBe(19);
        out(s, 2, 'commander_damage', 2);
      },
    },
    10: {
      mentions: [4],
      check: () => {
        const s = run(game(), cd(1, 0, 2, 4));
        const after = run(s, lc(2, 10));
        expect(tallies(after, 2)).toEqual({ '1:0': 4 });
        expect(seatOf(after, 2).life).toBe(46);
      },
    },
    11: { code: 'unknown_commander', bad: [[game(), cd(1, 1, 2, 1)]] },
    12: {
      code: 'invalid_amount',
      bad: [0, -1, 1000].map((a): [GameState, EventEnvelope] => [game(), cd(1, 0, 2, a)]),
    },
    13: { code: 'invalid_amount', bad: [[game(), cd(1, 5, 2, 1000)]] },
    14: {
      code: 'seat_eliminated',
      bad: [
        [run(game(), concede(2)), cd(1, 0, 2, 0)],
        [run(game(), concede(2)), cd(2, 5, 1, 1000)],
      ],
    },
    15: { code: 'invalid_amount', bad: [[rawGame({ starting_life: '40' }), cd(1, 0, 2, 1)]] },
    16: {
      code: 'invalid_amount',
      bad: [[rawGame({ commander_damage_threshold: '21' }), cd(1, 0, 2, 1)]],
    },
    17: {
      mentions: [1],
      check: () => {
        const s = rawWith({ commander_damage_threshold: 0 }, cd(1, 0, 2, 1));
        expect(tallies(s, 2)).toEqual({ '1:0': 1 });
        out(s, 2, 'commander_damage', 1);
      },
    },
    18: { code: 'invalid_phase', bad: [[lobbyOf(), cd(1, 0, 2, 1)]] },
    19: {
      code: 'invalid_amount',
      bad: [[withSeat(game(), 2, { life: -MAX_SAFE }), cd(1, 0, 2, 1)]],
    },
  };

  it('has a spec for each of the nineteen rows', () => {
    expect(Object.keys(SPECS).map(Number)).toEqual(Array.from({ length: 19 }, (_, i) => i + 1));
  });

  it.each(Array.from({ length: 19 }, (_, i) => [i + 1]))('row %i', (n) => {
    const spec = SPECS[n]!;
    const result = row(n);
    for (const m of spec.mentions ?? [])
      expect(result).toMatch(new RegExp(`(^|[^0-9])-?${m}([^0-9]|$)`));
    if (spec.code !== undefined) {
      expect(result).toContain(`\`${spec.code}\``);
      for (const [state, e] of spec.bad!) expect(codeOf(state, e)).toBe(spec.code);
    } else {
      expect(result).not.toMatch(/`(invalid_[a-z]+|seat_eliminated|unknown_[a-z]+)`/);
      spec.check!();
    }
  });
});

// ---------------------------------------------------------------------------------------------
describe('canonical order of commander_damage (5.2, 4.6)', () => {
  const HITS: Array<[number, number, number, number]> = [
    [4, 1, 3, 2],
    [3, 0, 1, 3],
    [2, 0, 4, 4],
    [1, 0, 2, 5],
    [4, 0, 3, 6],
    [1, 0, 4, 7],
  ];
  const table = () => game({ partners: [4] });
  const block = (state: GameState): string =>
    /"commander_damage":(\{.*?\}),"control_mode"/.exec(canon(state))![1]!;

  it('lists the targets by seat and the sources by seat then index', () => {
    const state = run(table(), ...HITS.map(([s, c, t, a]) => cd(s, c, t, a)));
    expect(block(state)).toBe(
      '{"1":{"3:0":3},"2":{"1:0":5},"3":{"4:0":6,"4:1":2},"4":{"1:0":7,"2:0":4}}',
    );
  });

  it('does not depend on the order the entries were created in', () => {
    const forward = run(table(), ...HITS.map(([s, c, t, a]) => cd(s, c, t, a)));
    const backward = run(table(), ...[...HITS].reverse().map(([s, c, t, a]) => cd(s, c, t, a)));
    const rotated = run(
      table(),
      ...[...HITS.slice(3), ...HITS.slice(0, 3)].map(([s, c, t, a]) => cd(s, c, t, a)),
    );
    expect(canon(backward)).toBe(canon(forward));
    expect(canon(rotated)).toBe(canon(forward));
  });

  it('seats 8 and 1 sort by code point: 1:0 before 8:0, targets 1, 5, 8', () => {
    const state = run(
      game({ count: 8 }),
      cd(8, 0, 1, 1),
      cd(1, 0, 8, 2),
      cd(8, 0, 5, 3),
      cd(1, 0, 5, 4),
    );
    expect(block(state)).toBe('{"1":{"8:0":1},"5":{"1:0":4,"8:0":3},"8":{"1:0":2}}');
  });

  it('an empty table is {} and a row is created by its first hit only', () => {
    expect(block(game())).toBe('{}');
    expect(Object.keys(run(game(), cd(1, 0, 2, 1)).commander_damage)).toEqual(['2']);
  });
});

// ---------------------------------------------------------------------------------------------
describe('purity: neither the input state nor the event is mutated', () => {
  const EVENTS: EventEnvelope[] = [
    cd(1, 0, 2, 5),
    cd(1, 0, 2, 21),
    cd(2, 1, 3, 7),
    cd(2, 0, 2, 21),
    cd(1, 0, 2, 0),
    cd(1, 1, 2, 1),
    cd(7, 0, 2, 1),
    cd(1, 0, 2, '1'),
  ];

  it.each(EVENTS.map((e, i) => [i, JSON.stringify(e.payload), e] as const))(
    'frozen state and event survive #%i %s, accepted or rejected',
    (_i, _payload, e) => {
      const state = deepFreeze(game({ partners: [2] }));
      const frozen = deepFreeze(structuredClone(e));
      const snapshot = canon(state);
      const result = apply(state, frozen);
      expect(canon(state)).toBe(snapshot);
      expect(JSON.stringify(frozen)).toBe(JSON.stringify(e));
      if (result.ok) expect(result.state).not.toBe(state);
    },
  );

  it('the same event on the same state gives the same result twice', () => {
    const state = run(game(), cd(1, 0, 2, 4));
    const a = apply(state, cd(1, 0, 2, 17));
    const b = apply(state, cd(1, 0, 2, 17));
    expect(canonicalJson(a.ok ? a.state : null)).toBe(canonicalJson(b.ok ? b.state : null));
  });

  it('a successful hit does not alias the rows or the seats of the input', () => {
    const state = run(game(), cd(1, 0, 2, 4));
    const result = apply(state, cd(1, 0, 2, 4));
    expect(result.ok && result.state.commander_damage['2']).not.toBe(state.commander_damage['2']);
    expect(result.ok && result.state.seats).not.toBe(state.seats);
    expect(tallies(state, 2)).toEqual({ '1:0': 4 });
    expect(seatOf(state, 2).life).toBe(36);
  });

  it('a hit on one row does not change another row object of the input', () => {
    const state = run(game(), cd(1, 0, 2, 4), cd(1, 0, 3, 5));
    const row3 = state.commander_damage['3'];
    run(state, cd(1, 0, 2, 4));
    expect(row3).toEqual({ '1:0': 5 });
  });
});

// ---------------------------------------------------------------------------------------------
describe('oracle: commander_damage against a function written from 4.1 and 4.6', () => {
  // Park-Miller generator: the same numbers in the Python twin, so both run the same cases.
  let seed = 614;
  const next = (): number => {
    seed = (seed * 48271) % 2147483647;
    return seed;
  };
  const pick = <T>(items: readonly T[]): T => items[next() % items.length]!;

  const ABSENT = Symbol('absent');
  const PHASES = [
    'active',
    'active',
    'active',
    'active',
    'ending',
    'lobby',
    'finished',
    'abandoned',
  ] as const;
  const COUNTS = [4, 4, 3] as const;
  const THRESHOLDS: unknown[] = [
    1,
    2,
    15,
    20,
    21,
    21,
    21,
    22,
    50,
    98,
    99,
    0,
    100,
    -5,
    '21',
    null,
    true,
  ];
  const LIVES: unknown[] = [
    -MAX_SAFE, -MAX_SAFE + 1, -MAX_SAFE + 5, -1000, -5, 0, 1, 5, 10, 21, 40, 40, 40, 100, 999,
    MAX_SAFE - 1, MAX_SAFE, '40', null, true,
  ]; // prettier-ignore
  const PRIORS: unknown[] = [ABSENT, ABSENT, 0, 3, 10, 14, 15, 19, 20, 21, 98, MAX_SAFE - 3];
  const SOURCES: unknown[] = [1, 1, 1, 2, 2, 3, 3, 4, 4, 0, 9, '1', null, ABSENT];
  const TARGETS: unknown[] = [1, 2, 2, 2, 3, 3, 4, 4, 0, 9, '2', null, ABSENT];
  const COMMANDERS: unknown[] = [0, 0, 0, 0, 1, 1, 1, 2, -1, '0', null, 1.5, ABSENT];
  const AMOUNTS: unknown[] = [
    -MAX_SAFE, -1000, -1, 0, 1, 1, 2, 5, 7, 12, 20, 21, 21, 25, 98, 99, 100, 998, 999, 1000,
    MAX_SAFE, '5', null, true, 1.5, ABSENT,
  ]; // prettier-ignore

  const BASE = game();
  const PARTNER_SEATS = [PARTNER_A, PARTNER_B];

  const isInt = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v);
  const isSeatId = (v: unknown): v is number => isInt(v) && v >= 1 && v <= 8;

  interface Case {
    state: GameState;
    hits: Payload[];
    label: string;
  }

  function build(): Case {
    const phase = pick(PHASES);
    const count = pick(COUNTS);
    const partner = [1, 2, 3, 4].map(() => next() % 3 === 0);
    const out = [1, 2, 3, 4].map(() => next() % 5 === 0);
    const lone = next() % 6 === 0; // only seat 2 is left standing: its first hit is its own
    const threshold = pick(THRESHOLDS);
    const lifeSeat = pick([1, 2, 3, 4]);
    const life = pick(LIVES);
    const prior = pick(PRIORS);
    const priorSource = pick([1, 2, 3]);
    let state: GameState = BASE;
    if (count === 3) state = { ...state, seats: state.seats.slice(0, 3), order: [1, 2, 3] };
    for (let id = 1; id <= count; id++) {
      if (partner[id - 1]) {
        state = withSeat(state, id, { commanders: PARTNER_SEATS, commander_cast: [0, 0] });
      }
    }
    for (let id = 1; id <= count; id++) {
      if (out[id - 1] || (lone && id !== 2)) state = outAt(state, id, 3);
    }
    state = {
      ...state,
      settings: { ...state.settings, commander_damage_threshold: threshold as number },
    };
    if (lifeSeat <= count) state = withSeat(state, lifeSeat, { life });
    if (prior !== ABSENT) {
      state = {
        ...state,
        commander_damage: { [String(lifeSeat)]: { [`${priorSource}:0`]: prior as number } },
      };
    }
    state = inPhase(state, phase);
    const hits: Payload[] = [];
    const total = 1 + (next() % 4);
    for (let i = 0; i < total; i++) {
      const payload: Payload = {};
      let source = pick(SOURCES);
      const commander = pick(COMMANDERS);
      let target = pick(TARGETS);
      const amount = pick(AMOUNTS);
      if (lone && i === 0) {
        source = 2;
        target = 2;
      }
      if (source !== ABSENT) payload.source_seat = source;
      if (commander !== ABSENT) payload.commander = commander;
      if (target !== ABSENT) payload.target_seat = target;
      if (amount !== ABSENT) payload.amount = amount;
      hits.push(payload);
    }
    return {
      state,
      hits,
      label: `${phase} count=${count} partner=${partner.join()} out=${out.join()} threshold=${JSON.stringify(threshold)} life[${lifeSeat}]=${JSON.stringify(life)} prior=${prior === ABSENT ? '-' : String(prior)}@${priorSource}:0`,
    };
  }

  /** The expected outcome, from the contract's words: an error code, or the expected state. */
  function oracle(before: Plain, payload: Payload): string | Plain {
    const p = JSON.parse(JSON.stringify(before)) as Plain;
    const { source_seat: s, commander: c, target_seat: t, amount: a } = payload;
    // 1. payload shape
    if (!isSeatId(s) || !isSeatId(t) || !isInt(c) || !isInt(a)) return 'invalid_payload';
    // 2. phase
    if (p.phase !== 'active' && p.phase !== 'ending') return 'invalid_phase';
    // 3. seats at the table, the source first
    const source = p.seats.find((x) => x.seat === s);
    const target = p.seats.find((x) => x.seat === t);
    if (source === undefined || target === undefined) return 'unknown_seat';
    // 4. eliminated target or source
    if (target.eliminated_at_seq !== null || source.eliminated_at_seq !== null) {
      return 'seat_eliminated';
    }
    // 5. the amount
    if (a < 1 || a > 999) return 'invalid_amount';
    // 6. the commander index of the source seat
    if (c < 0 || c >= source.commanders.length) return 'unknown_commander';
    // 7. numbers in the state
    const threshold = p.settings.commander_damage_threshold;
    const rowBefore = p.commander_damage[String(t)] ?? {};
    const key = `${s}:${c}`;
    const tally = (rowBefore[key] ?? 0) + a;
    const lifeBefore = target.life;
    if (!isInt(lifeBefore) || !isInt(threshold)) return 'invalid_amount';
    const lifeAfter = lifeBefore - a;
    if (Math.abs(lifeAfter) > MAX_SAFE || Math.abs(tally) > MAX_SAFE) return 'invalid_amount';
    // the change
    const standing = p.seats.filter((x) => x.eliminated_at_seq === null);
    p.commander_damage[String(t)] = { ...rowBefore, [key]: tally };
    target.life = lifeAfter;
    p.last_seq += 1;
    let reason: string | null = null;
    if (tally >= threshold) reason = 'commander_damage';
    else if (lifeAfter <= 0) reason = 'life';
    if (reason !== null) {
      target.elimination_reason = reason;
      target.eliminated_by = s;
      target.eliminated_at_seq = p.last_seq;
      target.eliminated_round = p.round;
      target.elimination_cause = null;
      if (standing.length === 1) p.no_winner = true;
    }
    return p;
  }

  const CASES = 3000;

  it(`agrees with the reducer on ${CASES} generated cases, hit after hit`, () => {
    const seen = new Map<string, number>();
    const bump = (kind: string) => seen.set(kind, (seen.get(kind) ?? 0) + 1);
    const mismatches: string[] = [];
    for (let i = 0; i < CASES; i++) {
      const c = build();
      let state = c.state;
      for (const payload of c.hits) {
        const expected = oracle(plain(state), payload);
        const result = apply(state, make('commander_damage', payload));
        const actual = result.ok ? canon(result.state) : result.error.code;
        const want = typeof expected === 'string' ? expected : canonicalJson(expected);
        if (actual !== want) {
          if (mismatches.length < 5) {
            mismatches.push(
              `${c.label} ${JSON.stringify(payload)}\n  want ${want}\n  got  ${actual}`,
            );
          }
          break;
        }
        if (typeof expected === 'string') {
          bump(expected);
          continue;
        }
        const t = expected.seats.find((x) => x.seat === payload.target_seat)!;
        let kind = 'ok';
        if (t.eliminated_at_seq === expected.last_seq) {
          kind = `ok_${t.elimination_reason}`;
          if (expected.no_winner && !plain(state).no_winner) kind = 'ok_full_draw';
        }
        bump(kind);
        if (payload.commander === 1) bump('ok_partner');
        if (payload.source_seat === payload.target_seat) bump('ok_self');
        if (Object.keys(expected.commander_damage[String(payload.target_seat)]!).length > 1) {
          bump('ok_two_keys');
        }
        state = (result as { ok: true; state: GameState }).state;
      }
    }
    expect(mismatches).toEqual([]);
    for (const kind of [
      'invalid_payload',
      'invalid_phase',
      'unknown_seat',
      'seat_eliminated',
      'invalid_amount',
      'unknown_commander',
      'ok',
      'ok_commander_damage',
      'ok_life',
      'ok_full_draw',
      'ok_partner',
      'ok_self',
      'ok_two_keys',
    ]) {
      expect(seen.get(kind) ?? 0, `the generator never produced ${kind}`).toBeGreaterThan(0);
    }
  });
});

// ---------------------------------------------------------------------------------------------
describe('the vectors of commander_damage are named after their content (8.6) and hold the criteria', () => {
  const dir = new URL('../vectors/', import.meta.url);
  const NAME = /^(accept|reject)-commander-damage-.*\.json$/;
  const files = readdirSync(dir).filter((f) => NAME.test(f));
  type Vector = {
    name: string;
    settings: Record<string, unknown>;
    events: Array<{ type: string; payload: Record<string, unknown> }>;
    expect: {
      state?: Plain;
      error?: { index: number; code: string };
    };
  };
  const load = (file: string): Vector =>
    JSON.parse(readFileSync(new URL(file, dir), 'utf8')) as Vector;

  // The code a reject vector's name promises, written from the names (the part before "-before-").
  function codeFromName(stem: string): string | undefined {
    const head = stem.replace(/^reject-commander-damage-/, '').split('-before-')[0]!;
    const table: Array<[RegExp, string]> = [
      [/^(unknown-seat|a-seat-that-left-the-lobby)$/, 'unknown_seat'],
      [/^(source|target)-seat-not-at-the-table$/, 'unknown_seat'],
      [/^(source|target)-seat-/, 'invalid_payload'],
      [/^(eliminated-|from-an?-|on-an?-|self-damage-of-an-eliminated)/, 'seat_eliminated'],
      [/^amount-(zero|negative-one|1000|minus-1000|a-million)$/, 'invalid_amount'],
      [/^amount-/, 'invalid_payload'],
      [
        /^commander-(1-of-a-single|2-of-a-partner|index-of-the-target|far-too-large|negative)/,
        'unknown_commander',
      ],
      [/^commander-1-after-the-partners-were-replaced$/, 'unknown_commander'],
      [/^commander-/, 'invalid_payload'],
      [/^unknown-commander$/, 'unknown_commander'],
      [/^invalid-amount$/, 'invalid_amount'],
      [/^invalid-phase$/, 'invalid_phase'],
      [/^(in-the-lobby)$/, 'invalid_phase'],
      [/^bad-payload$/, 'invalid_payload'],
      [/^(life-is-|threshold-is-|new-life-below-)/, 'invalid_amount'],
      [/^after-an-accepted-hit$/, 'invalid_amount'],
    ];
    return table.find(([re]) => re.test(head))?.[1];
  }

  it('finds at least 90 vectors for the type (92 came with the story, task T-6.1.4.3)', () => {
    expect(files.length).toBeGreaterThanOrEqual(90);
  });

  it('every vector the contract names exists', () => {
    const named = [...contract.matchAll(/`((?:accept|reject)-commander-damage-[a-z0-9-]+)`/g)].map(
      (m) => `${m[1]}.json`,
    );
    expect(named.length).toBeGreaterThanOrEqual(2);
    for (const file of named) expect(existsSync(new URL(file, dir)), file).toBe(true);
  });

  it.each(files)('%s', (file) => {
    const vector = load(file);
    const stem = file.replace(/\.json$/, '');
    expect(vector.name).toBe(stem);
    if (stem.startsWith('accept-')) {
      expect(vector.expect.state).toBeDefined();
      expect(vector.events.some((e) => e.type === 'commander_damage')).toBe(true);
      const final = vector.expect.state!;
      const eliminated = final.seats.filter((s) => s.eliminated_at_seq !== null);
      const created = vector.events.find((e) => e.type === 'game_created');
      const changed = vector.events.find((e) => e.type === 'settings_changed');
      const threshold = (): unknown => final.settings.commander_damage_threshold;
      if (/-stays?-in-the-game|-stay-/.test(stem)) {
        expect(
          eliminated.map((s) => s.seat),
          'a "stay" vector eliminates nobody',
        ).toEqual([]);
      }
      if (
        /(eliminates|eliminate|reason-is-commander-damage|reaching-the-threshold|reached-by-two-hits)$/.test(
          stem,
        )
      ) {
        expect(eliminated.some((s) => s.elimination_reason === 'commander_damage')).toBe(true);
      }
      if (/life-elimination$/.test(stem)) {
        expect(eliminated.map((s) => s.elimination_reason)).toEqual(['life']);
      }
      if (/partner/.test(stem)) {
        expect(final.seats.some((s) => s.commanders.length === 2)).toBe(true);
        expect(
          vector.events.some((e) => e.type === 'commander_damage' && e.payload.commander === 1),
        ).toBe(true);
      }
      if (/self-damage/.test(stem)) {
        expect(
          vector.events.some(
            (e) => e.type === 'commander_damage' && e.payload.source_seat === e.payload.target_seat,
          ),
        ).toBe(true);
      }
      const given = /threshold-(\d+)/.exec(stem);
      if (given !== null && !/threshold-and-life/.test(stem)) {
        expect(threshold()).toBe(Number(given[1]));
        const via = [
          vector.settings.commander_damage_threshold,
          (created?.payload.settings as Record<string, unknown> | undefined)
            ?.commander_damage_threshold,
          changed?.payload.commander_damage_threshold,
        ];
        expect(via).toContain(Number(given[1]));
      }
      if (/amount-(\d+)$/.test(stem)) {
        const amount = Number(/amount-(\d+)$/.exec(stem)![1]);
        expect(
          vector.events.some((e) => e.type === 'commander_damage' && e.payload.amount === amount),
        ).toBe(true);
      }
    } else {
      expect(vector.expect.error).toBeDefined();
      const { index, code } = vector.expect.error!;
      expect(vector.events[index]!.type).toBe('commander_damage');
      const promised = codeFromName(stem);
      expect(promised, `no code is promised by the name ${stem}`).toBeDefined();
      expect(code).toBe(promised);
    }
  });

  it('criterion 1 vector: three events of 7 from 1:0 to seat 3, tally 21, life 19, eliminated by 1', () => {
    const v = load('accept-commander-damage-three-hits-of-7-from-one-commander-eliminate.json');
    const hits = v.events.filter((e) => e.type === 'commander_damage');
    expect(hits.map((e) => e.payload)).toEqual(
      Array.from({ length: 3 }, () => ({
        source_seat: 1,
        commander: 0,
        target_seat: 3,
        amount: 7,
      })),
    );
    const final = v.expect.state!;
    expect(final.commander_damage).toEqual({ '3': { '1:0': 21 } });
    expect(final.seats.find((s) => s.seat === 3)).toMatchObject({
      life: 19,
      elimination_reason: 'commander_damage',
      eliminated_by: 1,
      eliminated_at_seq: final.last_seq,
    });
  });

  it('criterion 2 vector: partners A and B deal 12 each to seat 4, two tallies, 16 life, not eliminated', () => {
    const v = load('accept-commander-damage-partners-are-tracked-separately.json');
    const hits = v.events.filter((e) => e.type === 'commander_damage').map((e) => e.payload);
    expect(hits).toEqual([
      { source_seat: 2, commander: 0, target_seat: 4, amount: 12 },
      { source_seat: 2, commander: 1, target_seat: 4, amount: 12 },
    ]);
    const final = v.expect.state!;
    expect(final.commander_damage).toEqual({ '4': { '2:0': 12, '2:1': 12 } });
    expect(final.seats.find((s) => s.seat === 4)).toMatchObject({
      life: 16,
      elimination_reason: null,
      eliminated_at_seq: null,
    });
    expect(final.seats.find((s) => s.seat === 2)!.commanders).toHaveLength(2);
  });

  it('criterion 3 vector: the event that eliminates changes tally, life and elimination fields together', () => {
    const v = load(
      'accept-commander-damage-tally-and-life-and-elimination-change-in-one-event.json',
    );
    const final = v.expect.state!;
    expect(final.seats.find((s) => s.seat === 3)).toMatchObject({
      life: 19,
      elimination_reason: 'commander_damage',
      eliminated_at_seq: final.last_seq,
    });
    expect(final.commander_damage).toEqual({ '3': { '1:0': 21 } });
  });

  it('criterion 4 vector: tally 21 and life 0 at once gives commander_damage', () => {
    const v = load(
      'accept-commander-damage-threshold-and-life-at-once-the-reason-is-commander-damage.json',
    );
    const final = v.expect.state!;
    expect(final.commander_damage).toEqual({ '4': { '1:0': 21 } });
    expect(final.seats.find((s) => s.seat === 4)).toMatchObject({
      life: 0,
      elimination_reason: 'commander_damage',
      eliminated_by: 1,
    });
  });

  it('criterion 5 vectors: amounts 0, -1, 1000 and the commanders 1 and 2 are rejected with the story codes', () => {
    const want: Record<string, string> = {
      'reject-commander-damage-amount-zero': 'invalid_amount',
      'reject-commander-damage-amount-negative-one': 'invalid_amount',
      'reject-commander-damage-amount-1000': 'invalid_amount',
      'reject-commander-damage-commander-1-of-a-single-commander-seat': 'unknown_commander',
      'reject-commander-damage-commander-2-of-a-partner-seat': 'unknown_commander',
    };
    for (const [stem, code] of Object.entries(want)) {
      expect(load(`${stem}.json`).expect.error!.code, stem).toBe(code);
    }
  });

  it('criterion 6 vectors: threshold 15 reached by 7 and 8, with 14 one short', () => {
    const v = load('accept-commander-damage-threshold-15-reached-by-two-hits.json');
    expect(v.expect.state!.settings.commander_damage_threshold).toBe(15);
    expect(v.expect.state!.commander_damage).toEqual({ '2': { '1:0': 15 } });
    expect(v.expect.state!.seats.find((s) => s.seat === 2)).toMatchObject({
      life: 25,
      elimination_reason: 'commander_damage',
    });
    const short = load('accept-commander-damage-threshold-15-one-short-stays-in-the-game.json');
    expect(short.expect.state!.commander_damage).toEqual({ '2': { '1:0': 14 } });
  });

  it('the five kinds of task T-6.1.4.3 each have a vector', () => {
    const has = (re: RegExp) => files.some((f) => re.test(f));
    expect(has(/^accept-commander-damage-three-hits/)).toBe(true); // single commander to 21
    expect(has(/^accept-commander-damage-partners-are-tracked-separately/)).toBe(true);
    expect(has(/^accept-commander-damage-threshold-and-life-at-once/)).toBe(true); // precedence
    expect(
      has(/^reject-commander-damage-(source-seat-not-at-the-table|commander-1-of-a-single)/),
    ).toBe(true);
    expect(has(/^accept-commander-damage-threshold-15/)).toBe(true); // a non-default threshold
  });
});
