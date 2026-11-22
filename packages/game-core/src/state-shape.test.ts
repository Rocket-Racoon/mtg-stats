import { readdirSync, readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { initialState } from './state';
import { ELIMINATION_REASONS, WIN_CONDITIONS } from './types';

/**
 * Review rules for the state (CONTRACT.md 3.1 and 3.3): the `reverted` key, and the five flat seat
 * elimination fields with their null-ness invariant. Every state in the shared vectors (every
 * reducer vector's expected state and every state in `canonical-json-game-state.json`) and the
 * initial state are checked against the contract's tables and the invariant, so a vector cannot
 * carry a stale shape. The TS types cannot express the invariant, so it is checked on data.
 */

const contract = readFileSync(new URL('../CONTRACT.md', import.meta.url), 'utf8');
const dir = new URL('../vectors/', import.meta.url);

function tableKeys(heading: string): string[] {
  const start = contract.indexOf(`\n${heading}\n`);
  const rest = contract.slice(start + heading.length + 2);
  const end = rest.search(/\n#{1,3} /);
  const section = end < 0 ? rest : rest.slice(0, end);
  return [...section.matchAll(/^\| `([a-z_]+)` *\|/gm)].map((m) => m[1]!);
}

const TOP_KEYS = tableKeys('### 3.1 Top level');
const SEAT_KEYS = tableKeys('### 3.3 Seat object');
const ELIMINATION_FIELDS = [
  'elimination_reason',
  'eliminated_by',
  'eliminated_at_seq',
  'eliminated_round',
  'elimination_cause',
];

type Json = Record<string, unknown>;

function seatViolations(seat: Json): string[] {
  const out: string[] = [];
  const f = ELIMINATION_FIELDS.map((k) => seat[k]);
  const eliminated = seat.eliminated_at_seq !== null;
  if (ELIMINATION_FIELDS.some((k) => !(k in seat))) out.push('missing elimination field');
  if (!eliminated && f.some((v) => v !== null))
    out.push('in the game but an elimination field is set');
  if (eliminated) {
    if (!Number.isInteger(seat.eliminated_at_seq)) out.push('eliminated_at_seq is not an integer');
    if (!ELIMINATION_REASONS.includes(seat.elimination_reason as never)) out.push('bad reason');
    if (!Number.isInteger(seat.eliminated_round)) out.push('eliminated_round is not an integer');
  }
  const by = seat.eliminated_by;
  if (by !== null && !(Number.isInteger(by) && (by as number) >= 1 && (by as number) <= 8))
    out.push('bad eliminated_by');
  const cause = seat.elimination_cause;
  if (cause !== null && !WIN_CONDITIONS.includes(cause as never)) out.push('bad cause');
  return out;
}

function stateViolations(state: Json): string[] {
  const out: string[] = [];
  if (JSON.stringify(Object.keys(state).sort()) !== JSON.stringify([...TOP_KEYS].sort())) {
    out.push(`keys differ from 3.1: ${Object.keys(state).sort().join(',')}`);
  }
  for (const key of ['voided', 'reverted']) {
    const list = state[key] as number[];
    if (!Array.isArray(list)) out.push(`${key} is not an array`);
    else if (list.some((n, i) => !Number.isInteger(n) || (i > 0 && n <= list[i - 1]!))) {
      out.push(`${key} is not ascending without duplicates`);
    }
  }
  for (const seat of (state.seats as Json[]) ?? []) {
    if (JSON.stringify(Object.keys(seat).sort()) !== JSON.stringify([...SEAT_KEYS].sort())) {
      out.push(`seat ${String(seat.seat)} keys differ from 3.3`);
    }
    out.push(...seatViolations(seat).map((v) => `seat ${String(seat.seat)}: ${v}`));
  }
  return out;
}

interface NamedState {
  name: string;
  state: Json;
}
function collectStates(): NamedState[] {
  const found: NamedState[] = [{ name: 'initialState()', state: initialState('g') as never }];
  for (const file of readdirSync(dir).filter((f) => f.endsWith('.json'))) {
    const v = JSON.parse(readFileSync(new URL(file, dir), 'utf8')) as Json;
    if (v.kind === 'reducer' && (v.expect as Json).state) {
      found.push({ name: file, state: (v.expect as Json).state as Json });
    } else if (v.kind === 'canonical_json' && file === 'canonical-json-game-state.json') {
      for (const c of v.cases as { name: string; input: Json }[]) {
        found.push({ name: `${file}: ${c.name}`, state: c.input });
      }
    }
  }
  return found;
}
const states = collectStates();

describe('state shape in the contract tables', () => {
  it('has the `reverted` key and the five flat elimination fields in the tables', () => {
    expect(TOP_KEYS).toContain('reverted');
    expect(TOP_KEYS.indexOf('voided')).toBeLessThan(TOP_KEYS.indexOf('reverted'));
    expect(TOP_KEYS.at(-1)).toBe('last_seq');
    expect(SEAT_KEYS.slice(-5)).toEqual(ELIMINATION_FIELDS);
    expect(SEAT_KEYS).not.toContain('elimination');
  });

  it('keeps the nested elimination object out of every state', () => {
    for (const { name, state } of states) {
      for (const seat of state.seats as Json[])
        expect(seat, name).not.toHaveProperty('elimination');
    }
  });
});

describe('every state in the vectors follows 3.1 and 3.3', () => {
  it('collects the initial state, the reducer states and the three game states', () => {
    expect(states.length).toBeGreaterThan(18);
    expect(states.filter((s) => s.name.startsWith('canonical-json-game-state')).length).toBe(3);
  });

  it.each(states.map((s) => [s.name, s.state] as const))('%s', (_name, state) => {
    expect(stateViolations(state)).toEqual([]);
    expect(state.reverted).toBeDefined();
  });

  it('has eliminated and in-game seats, so the invariant is not vacuous', () => {
    const seats = states.flatMap((s) => (s.state.seats as Json[]) ?? []);
    expect(seats.some((s) => s.eliminated_at_seq !== null)).toBe(true);
    expect(seats.some((s) => s.eliminated_at_seq === null)).toBe(true);
    expect(seats.some((s) => s.eliminated_by === null && s.eliminated_at_seq !== null)).toBe(true);
    expect(seats.some((s) => s.elimination_cause !== null)).toBe(true);
  });

  it('starts with reverted, voided empty in the initial state', () => {
    const s = initialState('g');
    expect(s.reverted).toEqual([]);
    expect(s.voided).toEqual([]);
  });

  it('keeps raw characters in the game-state vector (NFD names, bidi and astral text survive)', () => {
    const text = readFileSync(new URL('canonical-json-game-state.json', dir), 'utf8');
    expect(text).toContain('é (NFD)');
    expect(text).toContain('‮');
    expect(text).toContain('\u{1f600}');
    expect(text.normalize('NFC')).not.toBe(text);
  });
});

describe('the invariant checker catches a broken seat (positive controls)', () => {
  const inGame: Json = {
    elimination_reason: null,
    eliminated_by: null,
    eliminated_at_seq: null,
    eliminated_round: null,
    elimination_cause: null,
  };
  const out: Json = {
    elimination_reason: 'life',
    eliminated_by: 1,
    eliminated_at_seq: 5,
    eliminated_round: 2,
    elimination_cause: null,
  };

  it('accepts an in-game seat and an eliminated seat', () => {
    expect(seatViolations(inGame)).toEqual([]);
    expect(seatViolations(out)).toEqual([]);
    expect(seatViolations({ ...out, eliminated_by: null, elimination_cause: 'combo' })).toEqual([]);
  });

  it.each([
    ['a reason on an in-game seat', { ...inGame, elimination_reason: 'life' }],
    ['a round on an in-game seat', { ...inGame, eliminated_round: 1 }],
    ['an eliminator on an in-game seat', { ...inGame, eliminated_by: 2 }],
    ['a cause on an in-game seat', { ...inGame, elimination_cause: 'combo' }],
    ['an eliminated seat without a reason', { ...out, elimination_reason: null }],
    ['an eliminated seat without a round', { ...out, eliminated_round: null }],
    ['an unknown reason', { ...out, elimination_reason: 'boredom' }],
    ['an eliminator outside 1 to 8', { ...out, eliminated_by: 9 }],
    ['an unknown cause', { ...out, elimination_cause: 'draw' }],
    ['a missing field', { ...inGame, eliminated_round: undefined } as unknown as Json],
  ])('flags %s', (_name, seat) => {
    const bad = { ...seat };
    if ('eliminated_round' in bad && bad.eliminated_round === undefined)
      delete bad.eliminated_round;
    expect(seatViolations(bad).length).toBeGreaterThan(0);
  });
});
