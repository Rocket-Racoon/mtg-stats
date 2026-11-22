import { describe, expect, it } from 'vitest';
import { canonicalJson } from './canonical-json';
import { eliminate } from './elimination';
import type { Elimination } from './elimination';
import { activeGame, make, run } from './lobby.test.helpers';
import type { GameState } from './types';

/**
 * The elimination routine (CONTRACT.md 4.5.2), called directly. No event eliminates several seats
 * at once before S-6.1.7 (group_drain), so criterion 6 of S-6.1.3 and the "every seat still in the
 * game at once" half of criterion 7 have no shared vector yet: they are pinned here, and S-6.1.7
 * adds the vectors.
 */

const life = (seat: number, reason: Elimination['reason'] = 'life'): Elimination => ({
  seat,
  reason,
  by: null,
  cause: null,
});

function deepFreeze<T>(value: T): T {
  if (typeof value === 'object' && value !== null && !Object.isFrozen(value)) {
    Object.freeze(value);
    for (const child of Object.values(value)) deepFreeze(child);
  }
  return value;
}

/** Round 2, seq 20, seats 1 to 4 in the game. */
const game = (): GameState => ({ ...activeGame(4), round: 2, last_seq: 19 });

const fieldsOf = (state: GameState, seat: number) => {
  const s = state.seats.find((x) => x.seat === seat)!;
  return [
    s.elimination_reason,
    s.eliminated_by,
    s.eliminated_at_seq,
    s.eliminated_round,
    s.elimination_cause,
  ];
};

describe('eliminate: several seats at once (criterion 6)', () => {
  it('gives every seat the same eliminated_at_seq and round, and its own reason, eliminator and cause', () => {
    const next = eliminate(game(), 20, [
      { seat: 2, reason: 'life', by: 1, cause: null },
      { seat: 3, reason: 'poison', by: null, cause: null },
      { seat: 4, reason: 'declared', by: null, cause: 'mill' },
    ]);
    expect(fieldsOf(next, 2)).toEqual(['life', 1, 20, 2, null]);
    expect(fieldsOf(next, 3)).toEqual(['poison', null, 20, 2, null]);
    expect(fieldsOf(next, 4)).toEqual(['declared', null, 20, 2, 'mill']);
    expect(fieldsOf(next, 1)).toEqual([null, null, null, null, null]);
    expect(next.no_winner).toBe(false);
  });

  it('does not depend on the order of the entries', () => {
    const a = eliminate(game(), 20, [life(2), life(4)]);
    const b = eliminate(game(), 20, [life(4), life(2)]);
    expect(canonicalJson(a)).toBe(canonicalJson(b));
  });

  it('keeps the seats sorted and changes nothing but the seats and no_winner', () => {
    const before = game();
    const next = eliminate(before, 20, [life(3), life(1)]);
    expect(next.seats.map((s) => s.seat)).toEqual([1, 2, 3, 4]);
    expect({ ...next, seats: before.seats, no_winner: before.no_winner }).toEqual(before);
  });
});

describe('eliminate: the full-draw rule (criterion 7)', () => {
  it('sets no_winner when the entries are every seat still in the game', () => {
    const next = eliminate(game(), 20, [life(1), life(2), life(3), life(4)]);
    expect(next.no_winner).toBe(true);
    expect(next.seats.map((s) => s.eliminated_at_seq)).toEqual([20, 20, 20, 20]);
    expect(next.winner_seat).toBe(null);
    expect(next.phase).toBe('active');
  });

  it('counts only the seats that were still in the game: earlier eliminations stay losses', () => {
    const before = run(game(), make('player_conceded', { seat: 1 }, { seq: 20 }));
    const next = eliminate(before, 21, [life(2), life(3), life(4)]);
    expect(next.no_winner).toBe(true);
    expect(next.seats.map((s) => s.eliminated_at_seq)).toEqual([20, 21, 21, 21]);
    expect(next.seats.map((s) => s.elimination_reason)).toEqual([
      'concession',
      'life',
      'life',
      'life',
    ]);
  });

  it('does not set no_winner while one seat is left', () => {
    const next = eliminate(game(), 20, [life(1), life(2), life(3)]);
    expect(next.no_winner).toBe(false);
  });

  it('sets no_winner when the one seat left is eliminated (the literal rule)', () => {
    const before = eliminate(game(), 20, [life(1), life(2), life(3)]);
    const next = eliminate(before, 21, [life(4)]);
    expect(next.no_winner).toBe(true);
    expect(next.seats.map((s) => s.eliminated_at_seq)).toEqual([20, 20, 20, 21]);
  });

  it('never sets no_winner back to false', () => {
    const drawn = eliminate(game(), 20, [life(1), life(2), life(3), life(4)]);
    expect(eliminate(drawn, 21, [life(1)]).no_winner).toBe(true);
    expect(eliminate({ ...game(), no_winner: true }, 21, [life(1)]).no_winner).toBe(true);
  });
});

describe('eliminate: entries it skips', () => {
  it('returns the same state for an empty list', () => {
    const before = game();
    expect(eliminate(before, 20, [])).toBe(before);
  });

  it('skips a seat that is not at the table', () => {
    const before = game();
    expect(eliminate(before, 20, [life(7)])).toBe(before);
    const next = eliminate(before, 20, [life(7), life(2)]);
    expect(next.seats.map((s) => s.eliminated_at_seq)).toEqual([null, 20, null, null]);
  });

  it('skips a seat that is already eliminated and keeps its fields', () => {
    const once = eliminate(game(), 20, [{ seat: 2, reason: 'life', by: 1, cause: null }]);
    const twice = eliminate(once, 21, [life(2, 'concession'), life(3)]);
    expect(fieldsOf(twice, 2)).toEqual(['life', 1, 20, 2, null]);
    expect(fieldsOf(twice, 3)).toEqual(['life', null, 21, 2, null]);
  });

  it('takes the first entry of a seat named twice', () => {
    const next = eliminate(game(), 20, [life(2, 'poison'), life(2, 'life')]);
    expect(fieldsOf(next, 2)).toEqual(['poison', null, 20, 2, null]);
  });

  it('does not set no_winner when nothing was applied, even with no seat in the game', () => {
    const out = eliminate(game(), 20, [life(1), life(2), life(3), life(4)]);
    expect(eliminate(out, 21, [life(1)])).toBe(out);
    expect(eliminate({ ...out, no_winner: false }, 21, [life(1)]).no_winner).toBe(false);
  });
});

describe('eliminate: purity', () => {
  it('does not mutate its input, and does not touch last_seq', () => {
    const before = deepFreeze(game());
    const next = eliminate(before, 20, [life(1), life(2)]);
    expect(next.last_seq).toBe(19);
    expect(before.seats.every((s) => s.eliminated_at_seq === null)).toBe(true);
  });
});
