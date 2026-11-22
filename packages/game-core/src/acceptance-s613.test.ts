import { readdirSync, readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { canonicalJson } from './canonical-json';
import { eliminate } from './elimination';
import type { Elimination } from './elimination';
import { codeOf, joined, lobbyState, make, NO_OP_TYPES, run, started } from './lobby.test.helpers';
import { apply } from './reducer';
import { EVENT_TYPES, REDUCER_ERROR_CODES, WIN_CONDITIONS } from './types';
import type { EventEnvelope, EventType, GameState, Phase, Seat } from './types';

/**
 * Acceptance tests of S-6.1.3 (life totals, concession and life-based elimination), written from
 * the seven acceptance criteria of the story and from CONTRACT.md 3.3, 4.1, 4.5 and 6.2, not from
 * the handlers. The shared vectors are the primary proof for everything reachable through events.
 * This file adds what a vector cannot say: the elimination routine called with several seats
 * (no event eliminates two seats before S-6.1.7), phases a log cannot reach yet (`ending`,
 * `finished`, `abandoned`), every registered event type against an eliminated seat, the check
 * order parsed from the contract, and a brute-force oracle written from the contract's words.
 *
 * The Python twin is `apps/api/app/games/tests/test_s613_acceptance.py`: same cases, same numbers.
 */

const contract = readFileSync(new URL('../CONTRACT.md', import.meta.url), 'utf8');
const MAX_SAFE = 9007199254740991;

type Payload = Record<string, unknown>;

const ev = (type: string, payload: Payload = {}): EventEnvelope => make(type, payload);
const lc = (seat: number, delta: unknown, source?: unknown): EventEnvelope =>
  make(
    'life_changed',
    source === undefined ? { seat, delta } : { seat, delta, source_seat: source },
  );
const concede = (seat: number): EventEnvelope => make('player_conceded', { seat });
const declare = (seat: number, cause?: unknown): EventEnvelope =>
  make('player_eliminated', cause === undefined ? { seat } : { seat, cause });

/** A started game: seats 1 to `count`, `startingLife` from the settings, seat 1 first. */
function game(count = 4, startingLife: unknown = 40): GameState {
  return run(
    lobbyState({ starting_life: startingLife as number }),
    ...Array.from({ length: count }, (_, i) => joined(i + 1)),
    started(1),
  );
}

const seatOf = (state: GameState, id: number): Seat => state.seats.find((s) => s.seat === id)!;
const inGame = (state: GameState): number[] =>
  state.seats.filter((s) => s.eliminated_at_seq === null).map((s) => s.seat);
const inPhase = (state: GameState, phase: Phase): GameState => ({
  ...state,
  phase,
  resume_phase: phase === 'abandoned' ? 'active' : null,
});
const withSeat = (state: GameState, id: number, changes: Partial<Seat>): GameState => ({
  ...state,
  seats: state.seats.map((s) => (s.seat === id ? { ...s, ...changes } : s)),
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

/** The first reducer error code in each numbered item of a section, in order. */
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

/** The rows of the first table of `text` whose first cell is a number: cells trimmed. */
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

const ints = (cell: string): number[] => [...cell.matchAll(/\d+/g)].map((m) => Number(m[0]));

const CHECKS_4_5_1 = numberedCodes(section('#### 4.5.1 '));
const CHECKS_LIFE = numberedCodes(section('#### 4.5.3 '));
const CHECKS_CONCEDE = numberedCodes(section('#### 4.5.4 '));
const CHECKS_DECLARE = numberedCodes(section('#### 4.5.5 '));

const D5_CODES: string[] = [
  ...section('### 3.4 ')
    .split('\n')
    .filter((l) => l.startsWith('`combat_damage`'))[0]!
    .matchAll(/`([a-z_]+)`/g),
].map((m) => m[1]!);

// ---------------------------------------------------------------------------------------------
describe('the contract text this file relies on', () => {
  it('lists the ten D5 codes of 3.4, the ones the code knows', () => {
    expect(D5_CODES).toHaveLength(10);
    expect([...D5_CODES].sort()).toEqual([...WIN_CONDITIONS].sort());
  });

  it('4.5.1 fixes the order: payload shape, phase, unknown_seat, seat_eliminated', () => {
    expect(CHECKS_4_5_1).toEqual([
      'invalid_payload',
      'invalid_phase',
      'unknown_seat',
      'seat_eliminated',
    ]);
  });

  it('4.5.3 life_changed checks in that order, then the amount rules', () => {
    expect(CHECKS_LIFE).toEqual([
      'invalid_payload',
      'invalid_phase',
      'unknown_seat',
      'seat_eliminated',
      'invalid_amount',
      'invalid_amount',
    ]);
  });

  it('4.5.4 player_conceded and 4.5.5 player_eliminated use the shared order and nothing more', () => {
    expect(CHECKS_CONCEDE).toEqual(CHECKS_4_5_1);
    expect(CHECKS_DECLARE).toEqual(CHECKS_4_5_1);
  });
});

// ---------------------------------------------------------------------------------------------
describe('criterion 1: starting life', () => {
  it('a game started with the default settings has 40 life on every seat', () => {
    const state = run(lobbyState(), joined(1), joined(2), joined(3), joined(4), started(1));
    expect(state.settings.starting_life).toBe(40);
    expect(state.seats.map((s) => s.life)).toEqual([40, 40, 40, 40]);
  });

  it('starting_life 30 from the initial settings gives every seat 30', () => {
    expect(game(4, 30).seats.map((s) => s.life)).toEqual([30, 30, 30, 30]);
  });

  it('starting_life 30 from game_created (house rules) gives every seat 30', () => {
    const created = ev('game_created', {
      settings: {
        starting_life: 30,
        commander_damage_threshold: 21,
        poison_threshold: 10,
        trust_mode: 'casual',
      },
    });
    const state = run(lobbyState(), created, joined(1), joined(2), started(2));
    expect(state.seats.map((s) => s.life)).toEqual([30, 30]);
  });

  it('a lobby override with settings_changed gives every seat 30, seats that joined before and after', () => {
    const state = run(
      lobbyState(),
      joined(1),
      ev('settings_changed', { starting_life: 30 }),
      joined(2),
      started(1),
    );
    expect(state.seats.map((s) => s.life)).toEqual([30, 30]);
  });

  it.each([1, 999])(
    'starting_life %i is accepted by game_created and settings_changed',
    (value) => {
      const settings = {
        starting_life: value,
        commander_damage_threshold: 21,
        poison_threshold: 10,
        trust_mode: 'casual',
      };
      expect(codeOf(lobbyState(), ev('game_created', { settings }))).toBe('ok');
      expect(codeOf(lobbyState(), ev('settings_changed', { starting_life: value }))).toBe('ok');
    },
  );

  it.each([0, -1, 1000, 1001, MAX_SAFE, '40', true, null])(
    'starting_life %j is invalid_setting in game_created and in settings_changed',
    (value) => {
      const settings = {
        starting_life: value,
        commander_damage_threshold: 21,
        poison_threshold: 10,
        trust_mode: 'casual',
      };
      expect(codeOf(lobbyState(), ev('game_created', { settings }))).toBe('invalid_setting');
      expect(codeOf(lobbyState(), ev('settings_changed', { starting_life: value }))).toBe(
        'invalid_setting',
      );
    },
  );

  it('a rejected starting_life leaves every seat at the life it had', () => {
    const lobby = run(lobbyState(), joined(1), joined(2));
    expect(apply(lobby, ev('settings_changed', { starting_life: 1000 }))).toEqual({
      ok: false,
      error: { code: 'invalid_setting' },
    });
    expect(lobby.seats.map((s) => s.life)).toEqual([40, 40]);
  });
});

// ---------------------------------------------------------------------------------------------
describe('criterion 2: life 0 from a source eliminates with reason life', () => {
  // Seat 3 at 3 life: a table whose starting life is 3.
  const table = () => game(4, 3);
  const hit = () => lc(3, -3, 1);

  it('seat 3 at 3 life, -3 from seat 1: life 0, eliminated by life, eliminated_by 1', () => {
    const before = table();
    expect(seatOf(before, 3).life).toBe(3);
    const after = run(before, hit());
    const seat = seatOf(after, 3);
    expect(seat.life).toBe(0);
    expect(seat.elimination_reason).toBe('life');
    expect(seat.eliminated_by).toBe(1);
    expect(seat.elimination_cause).toBeNull();
  });

  it('eliminated_at_seq is the seq of that event (optimistic event: last_seq + 1)', () => {
    const before = table();
    const after = run(before, hit());
    expect(after.last_seq).toBe(before.last_seq + 1);
    expect(seatOf(after, 3).eliminated_at_seq).toBe(after.last_seq);
  });

  it('eliminated_at_seq is the seq the event carries when the server assigned it', () => {
    const before = table();
    const seq = before.last_seq + 1;
    const after = run(
      before,
      make('life_changed', { seat: 3, delta: -3, source_seat: 1 }, { seq }),
    );
    expect(seatOf(after, 3).eliminated_at_seq).toBe(seq);
  });

  it('the same elimination after other events carries a later seq (not a constant)', () => {
    const before = run(table(), lc(1, -1), lc(2, -1), lc(4, -1)); // seats at 2: still in the game
    const after = run(before, hit());
    expect(seatOf(after, 3).eliminated_at_seq).toBe(before.last_seq + 1);
    expect(seatOf(after, 3).eliminated_at_seq).toBe(9);
  });

  it('the elimination records the round of the moment, and the round does not change', () => {
    let state = table();
    for (let i = 0; i < 4; i++) state = run(state, ev('turn_passed')); // round 2, turn 5
    expect([state.round, state.turn]).toEqual([2, 5]);
    const after = run(state, hit());
    expect(seatOf(after, 3).eliminated_round).toBe(2);
    expect([after.round, after.turn]).toEqual([2, 5]);
  });

  it('the other seats, the phase and the active seat are untouched; nobody wins yet', () => {
    const before = table();
    const after = run(before, hit());
    for (const id of [1, 2, 4]) expect(seatOf(after, id)).toEqual(seatOf(before, id));
    expect([after.phase, after.active_seat, after.no_winner, after.winner_seat]).toEqual([
      'active',
      1,
      false,
      null,
    ]);
    expect(inGame(after)).toEqual([1, 2, 4]);
  });

  it('the same numbers reached from 40 life: -37 then -3 from seat 1', () => {
    const after = run(game(4), lc(3, -37), lc(3, -3, 1));
    expect(seatOf(after, 3)).toMatchObject({
      life: 0,
      elimination_reason: 'life',
      eliminated_by: 1,
      eliminated_at_seq: 7,
    });
  });

  it('one life left is still in the game: life 1 after -2 from 3', () => {
    const after = run(table(), lc(3, -2, 1));
    expect(seatOf(after, 3)).toMatchObject({ life: 1, eliminated_at_seq: null });
    expect(inGame(after)).toEqual([1, 2, 3, 4]);
  });
});

// ---------------------------------------------------------------------------------------------
describe('criterion 3: life below zero is recorded, not clamped', () => {
  const table = () => game(4, 5); // seat 2 at 5

  it('seat 2 at 5, delta -8: life -3, eliminated with eliminated_by null', () => {
    const after = run(table(), lc(2, -8));
    expect(seatOf(after, 2)).toMatchObject({
      life: -3,
      elimination_reason: 'life',
      eliminated_by: null,
      eliminated_at_seq: 6,
      eliminated_round: 1,
    });
  });

  it('a null source_seat is the same as none', () => {
    const after = run(table(), lc(2, -8, null));
    expect(seatOf(after, 2)).toMatchObject({ life: -3, eliminated_by: null });
  });

  it('a source seat that is the target itself is stored as eliminated_by', () => {
    expect(seatOf(run(table(), lc(2, -8, 2)), 2).eliminated_by).toBe(2);
  });

  it('there is no upper clamp either: +999 on a 5-life seat gives 1004', () => {
    expect(seatOf(run(table(), lc(2, 999)), 2).life).toBe(1004);
  });

  it('the most negative single hit: -999 on 5 life is -994', () => {
    expect(seatOf(run(table(), lc(2, -999)), 2).life).toBe(-994);
  });
});

// ---------------------------------------------------------------------------------------------
describe('criterion 4: an eliminated seat takes no event, except a revert or a host correction', () => {
  const base = () => outAt(game(4), 2, 5);

  it('life_changed on seat 2, with a gain or a loss, is seat_eliminated', () => {
    expect(codeOf(base(), lc(2, -1))).toBe('seat_eliminated');
    expect(codeOf(base(), lc(2, 1))).toBe('seat_eliminated');
  });

  it('life_changed from seat 2 as the source, with a gain or a loss, is seat_eliminated', () => {
    expect(codeOf(base(), lc(1, -1, 2))).toBe('seat_eliminated');
    expect(codeOf(base(), lc(1, 1, 2))).toBe('seat_eliminated');
  });

  it('player_conceded and player_eliminated on seat 2 are seat_eliminated', () => {
    expect(codeOf(base(), concede(2))).toBe('seat_eliminated');
    expect(codeOf(base(), declare(2))).toBe('seat_eliminated');
    expect(codeOf(base(), declare(2, 'mill'))).toBe('seat_eliminated');
  });

  it('a seat eliminated by an event is then closed to every kind of the three events', () => {
    const state = run(game(4), lc(2, -40, 1));
    expect(codeOf(state, lc(2, -1))).toBe('seat_eliminated');
    expect(codeOf(state, concede(2))).toBe('seat_eliminated');
    expect(codeOf(state, declare(2, 'combo'))).toBe('seat_eliminated');
    const conceded = run(game(4), concede(2));
    expect(codeOf(conceded, lc(2, -1))).toBe('seat_eliminated');
    const declared = run(game(4), declare(2));
    expect(codeOf(declared, concede(2))).toBe('seat_eliminated');
  });

  it('a rejected event changes nothing', () => {
    const before = base();
    expect(apply(before, lc(2, -1))).toEqual({ ok: false, error: { code: 'seat_eliminated' } });
    expect(canon(before)).toBe(canon(base()));
  });

  // What each of the 29 registered types does when it names eliminated seat 2 in an active game.
  // kind: seat_eliminated = rejected by 4.5; exception = a revert or host correction (4.5.1);
  // phase = a documented rejection (lobby-only handlers); no_seat = names no seat;
  // pending = a no-op until its story lands (that story inherits criterion 4, see the report).
  type Kind = 'seat_eliminated' | 'exception' | 'phase' | 'no_seat' | 'pending';
  interface Row {
    kind: Kind;
    payloads: Payload[];
    code: string;
  }
  const SETTINGS = {
    starting_life: 40,
    commander_damage_threshold: 21,
    poison_threshold: 10,
    trust_mode: 'casual',
  };
  const ROWS: Record<EventType, Row> = {
    game_created: { kind: 'phase', payloads: [{ settings: SETTINGS }], code: 'invalid_phase' },
    settings_changed: { kind: 'phase', payloads: [{ starting_life: 30 }], code: 'invalid_phase' },
    seat_joined: {
      kind: 'phase',
      payloads: [{ seat: 2, player_id: null, name: 'x' }],
      code: 'invalid_phase',
    },
    seat_left: { kind: 'phase', payloads: [{ seat: 2 }], code: 'invalid_phase' },
    seat_moved: {
      kind: 'exception',
      payloads: [
        { from: 2, to: 3, correction: true },
        { from: 3, to: 2, correction: true },
      ],
      code: 'ok',
    },
    seat_claimed: { kind: 'pending', payloads: [{ seat: 2, player_id: 'p' }], code: 'ok' },
    deck_assigned: {
      kind: 'phase',
      payloads: [{ seat: 2, deck_version_id: 'd', commanders: [{ card_id: null, name: '' }] }],
      code: 'invalid_phase',
    },
    commanders_set: {
      kind: 'phase',
      payloads: [{ seat: 2, commanders: [{ card_id: null, name: '' }] }],
      code: 'invalid_phase',
    },
    control_mode_changed: { kind: 'pending', payloads: [{ mode: 'host_all' }], code: 'ok' },
    game_started: {
      kind: 'phase',
      payloads: [{ first_seat: 2, started_at: '2026-11-21T12:00:00.000Z' }],
      code: 'invalid_phase',
    },
    turn_passed: { kind: 'no_seat', payloads: [{}], code: 'ok' },
    life_changed: {
      kind: 'seat_eliminated',
      payloads: [
        { seat: 2, delta: -1 },
        { seat: 1, delta: -1, source_seat: 2 },
      ],
      code: 'seat_eliminated',
    },
    commander_damage: {
      kind: 'seat_eliminated',
      payloads: [
        { source_seat: 2, commander: 0, target_seat: 3, amount: 1 },
        { source_seat: 3, commander: 0, target_seat: 2, amount: 1 },
      ],
      code: 'seat_eliminated',
    },
    poison_changed: { kind: 'pending', payloads: [{ seat: 2, delta: 1 }], code: 'ok' },
    counter_changed: {
      kind: 'pending',
      payloads: [{ seat: 2, counter: 'energy', delta: 1 }],
      code: 'ok',
    },
    commander_cast: {
      kind: 'pending',
      payloads: [{ seat: 2, commander: 0, delta: 1 }],
      code: 'ok',
    },
    monarch_set: { kind: 'pending', payloads: [{ seat: 2 }], code: 'ok' },
    initiative_set: { kind: 'pending', payloads: [{ seat: 2 }], code: 'ok' },
    drain: {
      kind: 'pending',
      payloads: [
        { source_seat: 2, target_seat: 3, amount: 1 },
        { source_seat: 3, target_seat: 2, amount: 1 },
      ],
      code: 'ok',
    },
    group_drain: {
      kind: 'pending',
      payloads: [{ source_seat: 2, amount: 1, gain: true }],
      code: 'ok',
    },
    player_conceded: { kind: 'seat_eliminated', payloads: [{ seat: 2 }], code: 'seat_eliminated' },
    player_eliminated: {
      kind: 'seat_eliminated',
      payloads: [{ seat: 2 }, { seat: 2, cause: 'mill' }],
      code: 'seat_eliminated',
    },
    player_revived: { kind: 'exception', payloads: [{ seat: 2, reason: 'misclick' }], code: 'ok' },
    value_set: {
      kind: 'exception',
      payloads: [{ seat: 2, field: 'life', value: 10 }],
      code: 'ok',
    },
    game_end_requested: { kind: 'pending', payloads: [{}], code: 'ok' },
    game_ended: { kind: 'pending', payloads: [{ summary: {} }], code: 'ok' },
    game_abandoned: { kind: 'pending', payloads: [{ reason: 'host' }], code: 'ok' },
    game_resumed: { kind: 'pending', payloads: [{}], code: 'ok' },
    event_reverted: { kind: 'exception', payloads: [{ target_seq: 6 }], code: 'ok' },
  };

  it('the table covers every registered type, once', () => {
    expect(Object.keys(ROWS).sort()).toEqual([...EVENT_TYPES].sort());
  });

  it.each(EVENT_TYPES.map((t) => [t]))('%s against an eliminated seat 2', (type) => {
    const row = ROWS[type as EventType];
    for (const payload of row.payloads) {
      expect(codeOf(base(), ev(type, payload))).toBe(row.code);
    }
  });

  it('the types whose handlers are still no-ops are exactly the rows marked pending, or an exception that is not specified yet', () => {
    const unspecifiedExceptions: string[] = ['player_revived', 'value_set', 'event_reverted'];
    const noOps = Object.entries(ROWS)
      .filter(([type, row]) => row.kind === 'pending' || unspecifiedExceptions.includes(type))
      .map(([type]) => type)
      .sort();
    expect(noOps).toEqual([...NO_OP_TYPES].sort());
  });

  it('a no-op type accepts an eliminated seat and changes only last_seq (until its story lands)', () => {
    for (const [type, row] of Object.entries(ROWS)) {
      if (!(NO_OP_TYPES as readonly string[]).includes(type)) continue;
      for (const payload of row.payloads) {
        const before = base();
        const after = run(before, ev(type, payload));
        expect(canon(after)).toBe(canon({ ...before, last_seq: before.last_seq + 1 }));
      }
    }
  });

  it('seat_moved with correction true may name an eliminated seat and changes only the order', () => {
    const before = base();
    const after = run(before, ev('seat_moved', { from: 2, to: 4, correction: true }));
    expect(after.order).toEqual([1, 4, 3, 2]);
    expect(after.seats).toEqual(before.seats);
  });

  it('seat_moved without a correction in an active game is invalid_phase, whoever it names', () => {
    expect(codeOf(base(), ev('seat_moved', { from: 2, to: 4 }))).toBe('invalid_phase');
  });

  it('turn_passed skips the eliminated seat', () => {
    const state = outAt(game(4), 2, 5); // seat 1 active
    expect(run(state, ev('turn_passed')).active_seat).toBe(3);
  });

  it('4.5.1 names the same exceptions as the table above', () => {
    const text = section('#### 4.5.1 ');
    const start = text.indexOf('**What may still target an eliminated seat.**');
    const end = text.indexOf('No other S-6.1.6 handler');
    const bullets = text
      .slice(start, end)
      .split('\n')
      .filter((l) => l.startsWith('- '));
    const types = new Set<string>();
    for (const line of bullets) {
      for (const m of line.matchAll(/`([a-z_]+)`/g)) {
        if ((EVENT_TYPES as readonly string[]).includes(m[1]!)) types.add(m[1]!);
      }
    }
    const exceptions = Object.entries(ROWS)
      .filter(([, row]) => row.kind === 'exception')
      .map(([type]) => type)
      .sort();
    expect([...types].sort()).toEqual(exceptions);
  });
});

// ---------------------------------------------------------------------------------------------
describe('criterion 5: concession, host elimination and the amount rules', () => {
  it('player_conceded {seat: 4}: seat 4 is eliminated with reason concession', () => {
    const before = game(4);
    const after = run(before, concede(4));
    expect(seatOf(after, 4)).toMatchObject({
      life: 40,
      elimination_reason: 'concession',
      eliminated_by: null,
      eliminated_at_seq: after.last_seq,
      eliminated_round: 1,
      elimination_cause: null,
    });
    expect(inGame(after)).toEqual([1, 2, 3]);
  });

  it.each([40, 1, 999, -3])('a concession works whatever the life: %i', (value) => {
    const before = withSeat(game(4), 4, { life: value });
    const after = run(before, concede(4));
    expect(seatOf(after, 4)).toMatchObject({ life: value, elimination_reason: 'concession' });
  });

  it('a concession by the active seat leaves it the active seat until the turn passes', () => {
    const after = run(game(4), concede(1));
    expect(after.active_seat).toBe(1);
    expect(run(after, ev('turn_passed')).active_seat).toBe(2);
  });

  it.each(D5_CODES)(
    'player_eliminated {seat: 4, cause: %s} records reason declared and the cause',
    (cause) => {
      const after = run(game(4), declare(4, cause));
      expect(seatOf(after, 4)).toMatchObject({
        life: 40,
        elimination_reason: 'declared',
        eliminated_by: null,
        elimination_cause: cause,
        eliminated_at_seq: after.last_seq,
      });
    },
  );

  it('player_eliminated without a cause, or with a null cause, records cause null', () => {
    for (const e of [declare(4), declare(4, null)]) {
      expect(seatOf(run(game(4), e), 4)).toMatchObject({
        elimination_reason: 'declared',
        elimination_cause: null,
      });
    }
  });

  it('player_eliminated works whatever the life, and leaves life alone', () => {
    const before = withSeat(game(4), 4, { life: 17 });
    expect(seatOf(run(before, declare(4, 'mill')), 4).life).toBe(17);
  });

  it('player_eliminated does not name a winner or a win condition', () => {
    const after = run(game(4), declare(4, 'mill'));
    expect([after.winner_seat, after.win_condition, after.no_winner]).toEqual([null, null, false]);
  });

  it.each(['Mill', 'MILL', 'mill ', '', 'draw', 'win', 'life', 7, false, true, [], {}, ['mill']])(
    'a cause of %j is not a D5 code: invalid_payload',
    (cause) => {
      expect(codeOf(game(4), declare(4, cause))).toBe('invalid_payload');
    },
  );

  it.each([0, 1000, -1000, 1001, -1001, MAX_SAFE, -MAX_SAFE])(
    'life_changed with delta %i is invalid_amount',
    (delta) => {
      expect(codeOf(game(4), lc(1, delta))).toBe('invalid_amount');
    },
  );

  it.each([1, -1, 2, -2, 998, -998, 999, -999])(
    'life_changed with delta %i is accepted',
    (delta) => {
      expect(codeOf(game(4), lc(1, delta))).toBe('ok');
    },
  );

  it.each(['3', true, false, null, [], {}, 1.5, -0.5])(
    'life_changed with a delta of %j is invalid_payload, not invalid_amount',
    (delta) => {
      expect(codeOf(game(4), lc(1, delta))).toBe('invalid_payload');
    },
  );

  it('life_changed with no delta is invalid_payload', () => {
    expect(codeOf(game(4), make('life_changed', { seat: 1 }))).toBe('invalid_payload');
  });

  it('a delta outside the safe range is invalid_payload (6.2 step 5)', () => {
    expect(codeOf(game(4), lc(1, MAX_SAFE + 1))).toBe('invalid_payload');
    expect(codeOf(game(4), lc(1, -MAX_SAFE - 1))).toBe('invalid_payload');
  });

  it('a result outside the safe range is invalid_amount, in both directions, whatever the sign', () => {
    const high = withSeat(game(4), 1, { life: MAX_SAFE });
    expect(codeOf(high, lc(1, 1))).toBe('invalid_amount');
    expect(codeOf(high, lc(1, -1))).toBe('ok');
    const low = withSeat(game(4), 1, { life: -MAX_SAFE });
    expect(codeOf(low, lc(1, -1))).toBe('invalid_amount');
    expect(codeOf(low, lc(1, 1))).toBe('ok');
  });

  it.each([
    ['a string', '40'],
    ['null', null],
    ['true', true],
    ['false', false],
    ['a float', 1.5],
    ['an array', [40]],
    ['an object', { life: 40 }],
  ])(
    'life_changed on a seat whose life is %s is invalid_amount, never a throw',
    (_label, value) => {
      const before = withSeat(game(4), 1, { life: value as unknown as number });
      expect(codeOf(before, lc(1, -3))).toBe('invalid_amount');
      expect(codeOf(before, lc(1, 3))).toBe('invalid_amount');
    },
  );

  it('a concession and a declared elimination work on a seat whose life is not an integer', () => {
    const before = withSeat(game(4), 1, { life: '40' as unknown as number });
    expect(codeOf(before, concede(1))).toBe('ok');
    expect(codeOf(before, declare(1, 'mill'))).toBe('ok');
  });

  it('a seat that is at the table needs no deck or player: a guest seat concedes too', () => {
    const state = run(lobbyState(), joined(1, 'A', null), joined(2, 'B', null), started(1));
    expect(codeOf(state, concede(2))).toBe('ok');
  });
});

// ---------------------------------------------------------------------------------------------
describe('the elimination routine (criteria 6 and 7), called directly', () => {
  const entry = (
    seat: number,
    reason: Elimination['reason'] = 'life',
    by: number | null = null,
    cause: Elimination['cause'] = null,
  ): Elimination => ({ seat, reason, by, cause });

  /** A 4-seat game in round 3 where the seats not in `standing` were eliminated earlier. */
  function table(standing: number[]): GameState {
    let state: GameState = { ...game(4), round: 3, turn: 9, last_seq: 20 };
    [1, 2, 3, 4]
      .filter((id) => !standing.includes(id))
      .forEach((id, i) => {
        state = outAt(state, id, 6 + i, 2);
      });
    return state;
  }
  const SEQ = 21;

  it('criterion 6: two seats in one call get the same eliminated_at_seq', () => {
    const after = eliminate(table([1, 2, 3, 4]), SEQ, [entry(2), entry(3)]);
    expect([seatOf(after, 2).eliminated_at_seq, seatOf(after, 3).eliminated_at_seq]).toEqual([
      SEQ,
      SEQ,
    ]);
    expect(inGame(after)).toEqual([1, 4]);
    expect(after.no_winner).toBe(false);
  });

  it('criterion 6: three seats, each with its own reason, eliminator and cause, share the seq and the round', () => {
    const after = eliminate(table([1, 2, 3, 4]), SEQ, [
      entry(1, 'poison', 4),
      entry(2, 'commander_damage', 3),
      entry(3, 'declared', null, 'combo'),
    ]);
    expect(seatOf(after, 1)).toMatchObject({
      elimination_reason: 'poison',
      eliminated_by: 4,
      elimination_cause: null,
      eliminated_at_seq: SEQ,
      eliminated_round: 3,
    });
    expect(seatOf(after, 2)).toMatchObject({
      elimination_reason: 'commander_damage',
      eliminated_by: 3,
      eliminated_at_seq: SEQ,
      eliminated_round: 3,
    });
    expect(seatOf(after, 3)).toMatchObject({
      elimination_reason: 'declared',
      eliminated_by: null,
      elimination_cause: 'combo',
      eliminated_at_seq: SEQ,
      eliminated_round: 3,
    });
    expect(seatOf(after, 4).eliminated_at_seq).toBeNull();
  });

  it('criterion 7: every seat still in the game at once: no_winner true, none left standing', () => {
    const after = eliminate(table([1, 2, 3, 4]), SEQ, [entry(1), entry(2), entry(3), entry(4)]);
    expect(inGame(after)).toEqual([]);
    expect(after.no_winner).toBe(true);
    expect(after.seats.map((s) => s.eliminated_at_seq)).toEqual([SEQ, SEQ, SEQ, SEQ]);
  });

  it('criterion 7: the drawing seats are those eliminated by that event; earlier seats keep their own seq', () => {
    const before = table([3, 4]); // 1 out at 6, 2 out at 7
    const after = eliminate(before, SEQ, [entry(3), entry(4)]);
    expect(after.no_winner).toBe(true);
    expect(after.seats.map((s) => s.eliminated_at_seq)).toEqual([6, 7, SEQ, SEQ]);
    const last = Math.max(...after.seats.map((s) => s.eliminated_at_seq!));
    const drawing = after.seats.filter((s) => s.eliminated_at_seq === last).map((s) => s.seat);
    expect(drawing).toEqual([3, 4]);
    expect(seatOf(after, 1)).toEqual(seatOf(before, 1));
    expect(seatOf(after, 2)).toEqual(seatOf(before, 2));
  });

  it('criterion 7, literal rule: one seat left and eliminated is a full draw', () => {
    const after = eliminate(table([4]), SEQ, [entry(4, 'concession')]);
    expect(after.no_winner).toBe(true);
    expect(seatOf(after, 4).eliminated_at_seq).toBe(SEQ);
    expect(after.seats.slice(0, 3).map((s) => s.eliminated_at_seq)).toEqual([6, 7, 8]);
  });

  it('a partial elimination leaves no_winner as it is, true or false', () => {
    expect(eliminate(table([1, 2, 3, 4]), SEQ, [entry(2)]).no_winner).toBe(false);
    const drawn = { ...table([1, 2, 3, 4]), no_winner: true };
    expect(eliminate(drawn, SEQ, [entry(2)]).no_winner).toBe(true);
  });

  it('a seat named twice takes its first entry', () => {
    const after = eliminate(table([1, 2, 3, 4]), SEQ, [entry(2, 'poison', 1), entry(2, 'life', 3)]);
    expect(seatOf(after, 2)).toMatchObject({ elimination_reason: 'poison', eliminated_by: 1 });
    expect(inGame(after)).toEqual([1, 3, 4]);
  });

  it('a seat named twice that is the last two seats still counts as one seat each: full draw', () => {
    const after = eliminate(table([3, 4]), SEQ, [entry(3), entry(3), entry(4)]);
    expect(after.no_winner).toBe(true);
  });

  it('an entry for a seat not at the table or already eliminated is skipped without an error', () => {
    const before = table([2, 3, 4]); // seat 1 out at 6
    const after = eliminate(before, SEQ, [entry(5), entry(1, 'poison', 2), entry(3)]);
    expect(seatOf(after, 1)).toEqual(seatOf(before, 1));
    expect(inGame(after)).toEqual([2, 4]);
    expect(after.no_winner).toBe(false);
  });

  it('no entries, or only skipped ones, return the state as it is', () => {
    const before = table([1, 2]);
    expect(canon(eliminate(before, SEQ, []))).toBe(canon(before));
    expect(canon(eliminate(before, SEQ, [entry(5)]))).toBe(canon(before));
  });

  it('with nobody standing, an entry is skipped and no_winner is not set', () => {
    const before = table([]);
    expect(before.no_winner).toBe(false);
    expect(canon(eliminate(before, SEQ, [entry(1)]))).toBe(canon(before));
  });

  it('applying only some seats of the last two does not draw, and a seat left stays in the game', () => {
    const after = eliminate(table([3, 4]), SEQ, [entry(4)]);
    expect(after.no_winner).toBe(false);
    expect(inGame(after)).toEqual([3]);
    expect(after.winner_seat).toBeNull();
  });

  it('changes nothing but the elimination fields of the seats applied and no_winner', () => {
    const before: GameState = { ...table([1, 2, 3, 4]), monarch: 2, initiative: 3 };
    const withDamage: GameState = { ...before, commander_damage: { '2': { '1:0': 7 } } };
    const after = eliminate(withDamage, SEQ, [entry(1), entry(2), entry(3), entry(4)]);
    expect({ ...after, seats: [], no_winner: false }).toEqual({
      ...withDamage,
      seats: [],
      no_winner: false,
    });
    expect([after.phase, after.active_seat, after.last_seq, after.round, after.turn]).toEqual([
      'active',
      1,
      20,
      3,
      9,
    ]);
    expect([after.monarch, after.initiative, after.winner_seat, after.win_condition]).toEqual([
      2,
      3,
      null,
      null,
    ]);
    for (const [i, s] of after.seats.entries()) {
      expect({ ...s, ...Object.fromEntries(ELIMINATION_KEYS.map((k) => [k, null])) }).toEqual({
        ...withDamage.seats[i],
        ...Object.fromEntries(ELIMINATION_KEYS.map((k) => [k, null])),
      });
    }
  });

  it('does not mutate its input (frozen) and returns a new state', () => {
    const before = deepFreeze(table([1, 2, 3, 4]));
    const snapshot = canon(before);
    const entries = deepFreeze([entry(2), entry(3)]);
    const after = eliminate(before, SEQ, entries);
    expect(after).not.toBe(before);
    expect(canon(before)).toBe(snapshot);
  });

  // The worked examples of 4.5.2, parsed so that the table and the code cannot drift.
  const EXAMPLES: Array<{
    n: number;
    standing: number[];
    lists: Elimination[][];
    applied: number[];
    draw: boolean;
  }> = [
    { n: 1, standing: [1, 2, 3, 4], lists: [[entry(2)]], applied: [2], draw: false },
    { n: 2, standing: [1, 2, 3, 4], lists: [[entry(2), entry(3)]], applied: [2, 3], draw: false },
    {
      n: 3,
      standing: [1, 2, 3, 4],
      lists: [[entry(1), entry(2), entry(3), entry(4)]],
      applied: [1, 2, 3, 4],
      draw: true,
    },
    { n: 4, standing: [3, 4], lists: [[entry(3), entry(4)]], applied: [3, 4], draw: true },
    { n: 5, standing: [4], lists: [[entry(4)]], applied: [4], draw: true },
    {
      n: 6,
      standing: [1, 2, 3, 4],
      lists: [[entry(2, 'poison'), entry(2, 'life')]],
      applied: [2],
      draw: false,
    },
    { n: 7, standing: [1, 2], lists: [[], [entry(5)]], applied: [], draw: false },
    { n: 8, standing: [], lists: [[entry(1)]], applied: [], draw: false },
  ];

  const rows = tableRows(
    section('#### 4.5.2 ').slice(section('#### 4.5.2 ').indexOf('Worked examples')),
  );

  it('the worked-example table has the eight rows these cases are built from', () => {
    expect(rows.map((r) => Number(r[0]))).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);
  });

  it.each(EXAMPLES)(
    '4.5.2 example $n: standing, applied seats and no_winner match the table',
    (ex) => {
      const row = rows[ex.n - 1]!;
      expect(row[1] === 'none' ? [] : ints(row[1]!)).toEqual(ex.standing);
      const appliedCell = row[3]!;
      expect(appliedCell.startsWith('all') ? ex.standing : ints(appliedCell)).toEqual(ex.applied);
      expect(row[4]!.includes('true')).toBe(ex.draw);
      for (const list of ex.lists) {
        const before = table(ex.standing);
        const after = eliminate(before, SEQ, list);
        const changed = after.seats.filter(
          (s, i) => s.eliminated_at_seq !== before.seats[i]!.eliminated_at_seq,
        );
        expect(changed.map((s) => s.seat)).toEqual(ex.applied);
        expect(changed.every((s) => s.eliminated_at_seq === SEQ)).toBe(true);
        expect(after.no_winner).toBe(ex.draw);
      }
    },
  );

  it('example 6: the first entry for a seat decides the reason', () => {
    const after = eliminate(table([1, 2, 3, 4]), SEQ, [entry(2, 'poison'), entry(2, 'life')]);
    expect(seatOf(after, 2).elimination_reason).toBe('poison');
  });
});

const ELIMINATION_KEYS = [
  'elimination_reason',
  'eliminated_by',
  'eliminated_at_seq',
  'eliminated_round',
  'elimination_cause',
] as const;

// ---------------------------------------------------------------------------------------------
describe('criterion 7 through events: the last seats of the game', () => {
  it('two seats left, one eliminated: nobody wins yet and no_winner stays false', () => {
    const state = run(game(2), lc(2, -40, 1));
    expect(state.no_winner).toBe(false);
    expect(inGame(state)).toEqual([1]);
    expect(state.winner_seat).toBeNull();
    expect(state.phase).toBe('active');
  });

  it('eliminating the last seat in the game, after earlier eliminations, is a full draw (literal rule)', () => {
    const state = run(game(3), lc(1, -40), concede(2), lc(3, -40, 3));
    expect(state.no_winner).toBe(true);
    expect(state.seats.map((s) => s.eliminated_at_seq)).toEqual([5, 6, 7]);
    expect(state.phase).toBe('active');
    expect(state.winner_seat).toBeNull();
  });

  it('the last seat conceding, or declared out, is a full draw too', () => {
    expect(run(game(2), concede(1), concede(2)).no_winner).toBe(true);
    expect(run(game(2), declare(1, 'mill'), declare(2)).no_winner).toBe(true);
  });

  it('after a full draw every seat-naming event of S-6.1.3 is seat_eliminated and turn_passed is invalid_phase', () => {
    const state = run(game(2), concede(1), concede(2));
    expect(codeOf(state, lc(1, -1))).toBe('seat_eliminated');
    expect(codeOf(state, concede(2))).toBe('seat_eliminated');
    expect(codeOf(state, declare(1))).toBe('seat_eliminated');
    expect(codeOf(state, ev('turn_passed'))).toBe('invalid_phase');
  });
});

// ---------------------------------------------------------------------------------------------
describe('phases: lobby, finished and abandoned reject; active and ending accept', () => {
  const EVENTS: Array<[string, () => EventEnvelope]> = [
    ['life_changed', () => lc(2, -40)],
    ['player_conceded', () => concede(2)],
    ['player_eliminated', () => declare(2, 'mill')],
  ];

  it.each(['lobby', 'finished', 'abandoned'] as const)(
    '%s is invalid_phase for the three events',
    (phase) => {
      const state =
        phase === 'lobby' ? run(lobbyState(), joined(1), joined(2)) : inPhase(game(2), phase);
      for (const [, build] of EVENTS) expect(codeOf(state, build())).toBe('invalid_phase');
    },
  );

  it('nothing changes in the lobby: life stays the starting life', () => {
    const lobby = run(lobbyState(), joined(1), joined(2));
    expect(apply(lobby, lc(1, -1))).toEqual({ ok: false, error: { code: 'invalid_phase' } });
    expect(lobby.seats.map((s) => s.life)).toEqual([40, 40]);
  });

  it('active accepts the three events', () => {
    for (const [, build] of EVENTS) expect(codeOf(game(4), build())).toBe('ok');
  });

  describe('ending (hand-built)', () => {
    const ending = () => inPhase(game(4), 'ending');

    it('accepts the three events and stays in ending', () => {
      for (const [, build] of EVENTS) {
        const after = run(ending(), build());
        expect(after.phase).toBe('ending');
        expect(seatOf(after, 2).eliminated_at_seq).not.toBeNull();
      }
    });

    it('a life loss in ending records life, reason, eliminator and seq as in active', () => {
      const before = ending();
      const after = run(before, lc(2, -45, 3));
      expect(seatOf(after, 2)).toMatchObject({
        life: -5,
        elimination_reason: 'life',
        eliminated_by: 3,
        eliminated_at_seq: before.last_seq + 1,
      });
    });

    it('the other rules hold in ending: seat_eliminated, unknown_seat, invalid_amount, invalid_payload', () => {
      expect(codeOf(outAt(ending(), 2, 5), lc(2, -1))).toBe('seat_eliminated');
      expect(codeOf(ending(), lc(5, -1))).toBe('unknown_seat');
      expect(codeOf(ending(), lc(2, 0))).toBe('invalid_amount');
      expect(codeOf(ending(), lc(2, '1'))).toBe('invalid_payload');
    });

    it('a last seat eliminated in ending is a full draw and the phase stays ending', () => {
      const state = run(inPhase(game(2), 'ending'), concede(1), concede(2));
      expect([state.phase, state.no_winner]).toEqual(['ending', true]);
    });
  });
});

// ---------------------------------------------------------------------------------------------
describe('check order (4.5.1): shape, phase, unknown_seat, seat_eliminated, then the amount', () => {
  const lifeAfter = (state: GameState, e: EventEnvelope) => codeOf(state, e);

  it('an eliminated seat with an invalid amount is seat_eliminated', () => {
    const state = outAt(game(4), 2, 5);
    for (const delta of [0, 1000, -1000])
      expect(lifeAfter(state, lc(2, delta))).toBe('seat_eliminated');
  });

  it('an eliminated source with an invalid amount is seat_eliminated, and so is a gain', () => {
    const state = outAt(game(4), 2, 5);
    for (const delta of [0, 1000, 1, -1]) {
      expect(lifeAfter(state, lc(1, delta, 2))).toBe('seat_eliminated');
    }
  });

  it('an eliminated seat with a delta of the wrong type is invalid_payload', () => {
    expect(lifeAfter(outAt(game(4), 2, 5), lc(2, '5'))).toBe('invalid_payload');
  });

  it('an unknown seat with a wrong phase is invalid_phase', () => {
    expect(lifeAfter(inPhase(game(4), 'finished'), lc(5, -1))).toBe('invalid_phase');
    expect(codeOf(inPhase(game(4), 'finished'), concede(5))).toBe('invalid_phase');
    expect(codeOf(inPhase(game(4), 'abandoned'), declare(5))).toBe('invalid_phase');
  });

  it('an eliminated seat in a finished game is invalid_phase', () => {
    expect(codeOf(inPhase(outAt(game(4), 2, 5), 'finished'), lc(2, -1))).toBe('invalid_phase');
  });

  it('an unknown seat with an invalid amount is unknown_seat', () => {
    expect(lifeAfter(game(4), lc(5, 0))).toBe('unknown_seat');
    expect(lifeAfter(game(4), lc(1, 0, 5))).toBe('unknown_seat');
  });

  it('an unknown source with an eliminated target is unknown_seat; the target comes before the source', () => {
    expect(lifeAfter(outAt(game(4), 2, 5), lc(2, -1, 5))).toBe('unknown_seat');
    expect(lifeAfter(game(4), lc(5, -1, 6))).toBe('unknown_seat');
  });

  it('a wrong payload with a wrong phase is invalid_payload', () => {
    expect(codeOf(inPhase(game(4), 'finished'), make('life_changed', { seat: 1 }))).toBe(
      'invalid_payload',
    );
    expect(codeOf(run(lobbyState(), joined(1)), make('player_conceded', {}))).toBe(
      'invalid_payload',
    );
    expect(codeOf(inPhase(game(4), 'finished'), declare(1, 'nope'))).toBe('invalid_payload');
  });

  it('a bad cause with an unknown seat is invalid_payload, and a good cause with it is unknown_seat', () => {
    expect(codeOf(game(4), declare(6, 'nope'))).toBe('invalid_payload');
    expect(codeOf(game(4), declare(6, 'mill'))).toBe('unknown_seat');
  });

  // Every combination of violations of one handler, driven by the order parsed from the contract.
  type Ctx = { state: GameState; payload: Payload };
  const MAKERS: Record<string, (c: Ctx, handler: string) => Ctx> = {
    invalid_payload: (c, h) => ({
      ...c,
      payload: h === 'life_changed' ? { ...c.payload, delta: '5' } : { ...c.payload, seat: '2' },
    }),
    invalid_phase: (c) => ({ ...c, state: inPhase(c.state, 'finished') }),
    unknown_seat: (c, h) => ({
      ...c,
      payload: h === 'life_changed' ? { ...c.payload, source_seat: 5 } : { ...c.payload, seat: 5 },
    }),
    seat_eliminated: (c) => ({ ...c, state: outAt(c.state, 2, 5) }),
    invalid_amount: (c) => ({ ...c, payload: { ...c.payload, delta: 0 } }),
  };
  const HANDLERS: Array<[string, string[]]> = [
    ['life_changed', [...new Set(CHECKS_LIFE)]],
    ['player_conceded', CHECKS_CONCEDE],
    ['player_eliminated', CHECKS_DECLARE],
  ];

  it.each(HANDLERS)(
    '%s: every set of violations gives the earliest check that fails',
    (type, order) => {
      const failures: string[] = [];
      for (let mask = 1; mask < 1 << order.length; mask++) {
        const picked = order.filter((_, i) => (mask & (1 << i)) !== 0);
        let ctx: Ctx = {
          state: game(4),
          payload: type === 'life_changed' ? { seat: 2, delta: -1 } : { seat: 2 },
        };
        // Apply the latest check's violation first, so an earlier one that overwrites it wins.
        for (const code of [...picked].reverse()) ctx = MAKERS[code]!(ctx, type);
        const actual = codeOf(ctx.state, ev(type, ctx.payload));
        if (actual !== picked[0]) failures.push(`${picked.join('+')} gave ${actual}`);
      }
      expect(failures).toEqual([]);
    },
  );
});

// ---------------------------------------------------------------------------------------------
describe('4.5.3 worked examples, row by row (parsed so the table and the code cannot drift)', () => {
  const rows = tableRows(section('#### 4.5.3 '));
  type Spec = {
    code: string | null;
    state: () => GameState;
    events: EventEnvelope[];
    check?: (s: GameState) => void;
  };
  const SPECS: Record<number, Spec> = {
    1: {
      code: null,
      state: () => game(4),
      events: [lc(2, -5)],
      check: (s) => expect(seatOf(s, 2)).toMatchObject({ life: 35, eliminated_at_seq: null }),
    },
    2: {
      code: null,
      state: () => game(4),
      events: [lc(1, 10, 2)],
      check: (s) => expect(seatOf(s, 1)).toMatchObject({ life: 50, eliminated_at_seq: null }),
    },
    3: {
      code: null,
      state: () => game(4, 3),
      events: [lc(3, -3, 1)],
      check: (s) =>
        expect(seatOf(s, 3)).toMatchObject({
          life: 0,
          elimination_reason: 'life',
          eliminated_by: 1,
          eliminated_at_seq: s.last_seq,
          eliminated_round: 1,
        }),
    },
    4: {
      code: null,
      state: () => game(4, 5),
      events: [lc(2, -8)],
      check: (s) => expect(seatOf(s, 2)).toMatchObject({ life: -3, eliminated_by: null }),
    },
    5: {
      code: null,
      state: () => game(4, 5),
      events: [lc(1, -4)],
      check: (s) => expect(seatOf(s, 1)).toMatchObject({ life: 1, eliminated_at_seq: null }),
    },
    6: {
      code: null,
      state: () => game(4),
      events: [lc(2, -40, 2)],
      check: (s) => expect(seatOf(s, 2)).toMatchObject({ life: 0, eliminated_by: 2 }),
    },
    7: {
      code: null,
      state: () => game(4),
      events: [lc(2, -40, null)],
      check: (s) => expect(seatOf(s, 2)).toMatchObject({ life: 0, eliminated_by: null }),
    },
    8: {
      code: null,
      state: () => game(4, -5),
      events: [lc(1, 3)],
      check: (s) => expect(seatOf(s, 1)).toMatchObject({ life: -2, eliminated_at_seq: null }),
    },
    9: {
      code: null,
      state: () => game(4, -5),
      events: [lc(1, -1)],
      check: (s) => expect(seatOf(s, 1)).toMatchObject({ life: -6, elimination_reason: 'life' }),
    },
    10: {
      code: 'invalid_amount',
      state: () => game(4),
      events: [lc(1, 0), lc(1, 1000), lc(1, -1000)],
    },
    11: {
      code: 'invalid_payload',
      state: () => game(4),
      events: [lc(1, '3'), lc(1, true), lc(1, null), lc(1, 1.5), make('life_changed', { seat: 1 })],
    },
    12: { code: 'invalid_amount', state: () => game(4, '40'), events: [lc(1, -3)] },
    13: { code: 'invalid_amount', state: () => game(4, MAX_SAFE), events: [lc(1, 1)] },
    14: {
      code: 'seat_eliminated',
      state: () => outAt(game(4), 2, 5),
      events: [lc(2, 0), lc(1, -1, 2)],
    },
    15: {
      code: 'invalid_phase',
      state: () => run(lobbyState(), joined(1), joined(2)),
      events: [lc(1, -1)],
    },
    16: {
      code: null,
      state: () => run(game(3), concede(1), concede(2)),
      events: [lc(3, -40)],
      check: (s) => {
        expect(seatOf(s, 3).eliminated_at_seq).toBe(s.last_seq);
        expect(s.no_winner).toBe(true);
      },
    },
  };

  it('the table has sixteen rows', () => {
    expect(rows.map((r) => Number(r[0]))).toEqual(Array.from({ length: 16 }, (_, i) => i + 1));
  });

  it.each(Object.keys(SPECS).map((n) => [Number(n)]))('row %i', (n) => {
    const spec = SPECS[n]!;
    const result = rows[n - 1]![3]!;
    if (spec.code !== null) {
      expect(result).toContain(`\`${spec.code}\``);
      for (const e of spec.events) expect(codeOf(spec.state(), e)).toBe(spec.code);
    } else {
      expect(result).not.toMatch(/`(invalid_[a-z]+|seat_eliminated|unknown_seat)`/);
      for (const e of spec.events) spec.check!(run(spec.state(), e));
    }
  });
});

// ---------------------------------------------------------------------------------------------
describe('purity: neither the input state nor the event is mutated', () => {
  const EVENTS: EventEnvelope[] = [
    lc(2, -40, 1),
    lc(2, -3),
    lc(1, 5, 2),
    concede(3),
    declare(3, 'mill'),
    declare(3),
    lc(2, 0),
    concede(5),
  ];

  it.each(EVENTS.map((e, i) => [i, e.type, JSON.stringify(e.payload), e] as const))(
    'frozen state and event survive #%i %s %s, accepted or rejected',
    (_i, _type, _payload, e) => {
      const state = deepFreeze(game(4));
      const frozen = deepFreeze(structuredClone(e));
      const snapshot = canon(state);
      const result = apply(state, frozen);
      expect(canon(state)).toBe(snapshot);
      expect(JSON.stringify(frozen)).toBe(JSON.stringify(e));
      if (result.ok) expect(result.state).not.toBe(state);
    },
  );

  it('the same event on the same state gives the same result twice', () => {
    const state = game(4);
    const a = apply(state, lc(2, -40, 1));
    const b = apply(state, lc(2, -40, 1));
    expect(canonicalJson(a.ok ? a.state : null)).toBe(canonicalJson(b.ok ? b.state : null));
  });

  it('a successful elimination does not alias the seats of the input', () => {
    const state = game(4);
    const result = apply(state, lc(2, -40, 1));
    expect(result.ok && result.state.seats).not.toBe(state.seats);
    expect(seatOf(state, 2).eliminated_at_seq).toBeNull();
  });
});

// ---------------------------------------------------------------------------------------------
describe('oracle: life_changed against a function written from 4.5.1 and 4.5.3', () => {
  // Park-Miller generator: the same numbers in the Python twin, so both run the same cases.
  let seed = 613;
  const next = (): number => {
    seed = (seed * 48271) % 2147483647;
    return seed;
  };
  const pick = <T>(items: readonly T[]): T => items[next() % items.length]!;

  const ABSENT = Symbol('absent');
  const PHASES = ['active', 'active', 'ending', 'lobby', 'finished', 'abandoned'] as const;
  const TARGETS: unknown[] = [2, 2, 2, 2, 1, 3, 4, 0, 9, '2', null];
  const SOURCES: unknown[] = [ABSENT, ABSENT, null, 1, 2, 3, 3, 4, 9, 'x'];
  const DELTAS: unknown[] = [
    -MAX_SAFE,
    -1000,
    -999,
    -998,
    -41,
    -40,
    -39,
    -5,
    -2,
    -1,
    -1,
    0,
    1,
    1,
    2,
    5,
    998,
    999,
    1000,
    MAX_SAFE,
    '5',
    null,
    true,
    1.5,
    ABSENT,
  ];
  const LIVES: unknown[] = [
    -MAX_SAFE,
    -MAX_SAFE + 1,
    -1000,
    -999,
    -5,
    -1,
    0,
    1,
    2,
    5,
    40,
    40,
    40,
    998,
    999,
    1000,
    MAX_SAFE - 999,
    MAX_SAFE - 1,
    MAX_SAFE,
    '40',
    null,
    true,
  ];
  const CASES = 6000;

  type Plain = {
    phase: string;
    seats: Array<Record<string, unknown>>;
    last_seq: number;
    no_winner: boolean;
  } & Record<string, unknown>;

  interface Case {
    state: GameState;
    payload: Payload;
    label: string;
  }

  function build(): Case {
    const phase = pick(PHASES);
    const target = pick(TARGETS);
    const out1 = next() % 4 === 0;
    const out2 = next() % 3 === 0;
    const seat3 = pick(['alive', 'alive', 'out', 'absent'] as const);
    const life = pick(LIVES);
    const source = pick(SOURCES);
    const delta = pick(DELTAS);
    let state = game(3);
    if (seat3 === 'absent') {
      state = { ...state, seats: state.seats.slice(0, 2), order: [1, 2] };
    }
    if (out1) state = outAt(state, 1, 3);
    if (out2) state = outAt(state, 2, 3);
    if (seat3 === 'out') state = outAt(state, 3, 3);
    if (typeof target === 'number' && state.seats.some((s) => s.seat === target)) {
      state = withSeat(state, target, { life: life as number });
    }
    state = inPhase(state, phase);
    const payload: Payload = { seat: target };
    if (delta !== ABSENT) payload.delta = delta;
    if (source !== ABSENT) payload.source_seat = source;
    return {
      state,
      payload,
      label: `${phase} seat=${JSON.stringify(target)} out=${out1}/${out2}/${seat3} life=${JSON.stringify(life)} ${JSON.stringify(payload)}`,
    };
  }

  const isInt = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v);
  const isSeatId = (v: unknown): v is number => isInt(v) && v >= 1 && v <= 8;
  const given = (v: unknown): boolean => v !== undefined && v !== null;

  /** The expected outcome, from the contract's words: an error code, or the expected state. */
  function oracle(state: GameState, payload: Payload): string | Plain {
    const plain = JSON.parse(canon(state)) as Plain;
    const { seat, delta, source_seat: source } = payload;
    if (!isSeatId(seat) || !isInt(delta)) return 'invalid_payload';
    if (given(source) && !isSeatId(source)) return 'invalid_payload';
    if (plain.phase !== 'active' && plain.phase !== 'ending') return 'invalid_phase';
    const atTable = (id: number) => plain.seats.some((s) => s.seat === id);
    if (!atTable(seat) || (given(source) && !atTable(source as number))) return 'unknown_seat';
    const isOut = (id: number) =>
      plain.seats.find((s) => s.seat === id)!.eliminated_at_seq !== null;
    if (isOut(seat) || (given(source) && isOut(source as number))) return 'seat_eliminated';
    if (delta === 0 || Math.abs(delta) > 999) return 'invalid_amount';
    const target = plain.seats.find((s) => s.seat === seat)!;
    if (!isInt(target.life) || Math.abs(target.life + delta) > MAX_SAFE) return 'invalid_amount';
    const life = target.life + delta;
    target.life = life;
    if (delta < 0 && life <= 0) {
      const standing = plain.seats.filter((s) => s.eliminated_at_seq === null);
      target.elimination_reason = 'life';
      target.eliminated_by = given(source) ? source : null;
      target.eliminated_at_seq = plain.last_seq + 1;
      target.eliminated_round = plain.round;
      target.elimination_cause = null;
      if (standing.length === 1) plain.no_winner = true;
    }
    plain.last_seq += 1;
    return plain;
  }

  it(`agrees with the reducer on ${CASES} generated cases`, () => {
    const seen = new Map<string, number>();
    const mismatches: string[] = [];
    for (let i = 0; i < CASES; i++) {
      const c = build();
      const expected = oracle(c.state, c.payload);
      const result = apply(c.state, make('life_changed', c.payload));
      const actual = result.ok ? canon(result.state) : result.error.code;
      const want = typeof expected === 'string' ? expected : canonicalJson(expected);
      let kind = typeof expected === 'string' ? expected : 'ok';
      if (typeof expected !== 'string') {
        const t = expected.seats.find((s) => s.seat === c.payload.seat)!;
        kind = t.eliminated_at_seq === expected.last_seq ? 'ok_eliminated' : 'ok';
        if (kind === 'ok_eliminated' && expected.no_winner) kind = 'ok_full_draw';
      }
      seen.set(kind, (seen.get(kind) ?? 0) + 1);
      if (actual !== want && mismatches.length < 5)
        mismatches.push(`${c.label}\n  want ${want}\n  got  ${actual}`);
    }
    expect(mismatches).toEqual([]);
    for (const kind of [
      'invalid_payload',
      'invalid_phase',
      'unknown_seat',
      'seat_eliminated',
      'invalid_amount',
      'ok',
      'ok_eliminated',
      'ok_full_draw',
    ]) {
      expect(seen.get(kind) ?? 0, `the generator never produced ${kind}`).toBeGreaterThan(0);
    }
  });
});

// ---------------------------------------------------------------------------------------------
describe('the vectors of the three types are named after their content (8.6)', () => {
  const dir = new URL('../vectors/', import.meta.url);
  const files = readdirSync(dir).filter((f) =>
    /^(accept|reject)-(life-changed|player-conceded|player-eliminated)-.*\.json$/.test(f),
  );

  it('finds at least 14 vectors for the three types (task T-6.1.3.3)', () => {
    expect(files.length).toBeGreaterThanOrEqual(14);
  });

  it.each(files)('%s', (file) => {
    const m = /^(accept|reject)-(life-changed|player-conceded|player-eliminated)-/.exec(file)!;
    const type = m[2]!.replaceAll('-', '_');
    const vector = JSON.parse(readFileSync(new URL(file, dir), 'utf8')) as {
      events: Array<{ type: string }>;
      expect: { state?: unknown; error?: { index: number; code: string } };
    };
    if (m[1] === 'accept') {
      expect(vector.expect.state).toBeDefined();
      expect(vector.events.some((e) => e.type === type)).toBe(true);
    } else {
      expect(vector.expect.error).toBeDefined();
      expect(vector.events[vector.expect.error!.index]!.type).toBe(type);
    }
  });
});
