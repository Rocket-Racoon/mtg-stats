import { describe, expect, it } from 'vitest';
import { canonicalJson } from './canonical-json';
import { eliminate } from './elimination';
import type { Elimination } from './elimination';
import { activeGame, after, make, run } from './lobby.test.helpers';
import type { GameState, Phase } from './types';

/**
 * The monarch and initiative hand-off of the elimination routine (CONTRACT.md 4.5.2, S-6.1.5
 * criterion 4), on hand-built states. The shared vectors (`accept-hand-off-*`) pin every
 * eliminating event that exists; this file adds what only the routine called directly can say:
 * several seats at once, an `order` that is not 1..n, a missing `active_seat`, the `ending`
 * phase, a holder that was already out or is not at the table, and purity of the input.
 */

const out = (seat: number, reason: Elimination['reason'] = 'life'): Elimination => ({
  seat,
  reason,
  by: null,
  cause: null,
});
const holders = (s: GameState) => [s.monarch, s.initiative];
const withHolders = (
  s: GameState,
  monarch: number | null,
  initiative: number | null,
): GameState => ({
  ...s,
  monarch,
  initiative,
});
const withOrder = (s: GameState, order: number[]): GameState => ({ ...s, order });
const withActive = (s: GameState, active: number | null): GameState => ({
  ...s,
  active_seat: active,
});
const eliminated = (s: GameState, ...ids: number[]): GameState => ({
  ...s,
  seats: s.seats.map((x) =>
    ids.includes(x.seat)
      ? { ...x, elimination_reason: 'life', eliminated_at_seq: 1, eliminated_round: 1 }
      : x,
  ),
});
const inPhase = (s: GameState, phase: Phase): GameState => ({ ...s, phase });
const canon = (s: GameState) => canonicalJson(s);

const game = (): GameState => activeGame(4); // seat 1 active, order [1,2,3,4]

describe('the hand-off: who receives the monarch and the initiative', () => {
  it('goes to the active seat when it still stands (criterion 4, first sentence)', () => {
    const next = eliminate(withHolders(game(), 3, 4), 9, [out(3), out(4)]);
    expect(holders(next)).toEqual([1, 1]);
  });

  it('goes to the next seat in turn order when the holder is the active seat', () => {
    const s = withActive(withHolders(game(), 3, 3), 3);
    expect(holders(eliminate(s, 9, [out(3)]))).toEqual([4, 4]);
  });

  it('wraps from the end of the order to its head', () => {
    const s = withActive(withHolders(game(), 4, null), 4);
    expect(holders(eliminate(s, 9, [out(4)]))).toEqual([1, null]);
  });

  it('skips seats that were eliminated earlier', () => {
    const s = eliminated(withActive(withHolders(game(), 2, null), 2), 3, 4);
    expect(holders(eliminate(s, 9, [out(2)]))).toEqual([1, null]);
  });

  it('reads the order as it stands, which is not 1..n after a seat_moved correction', () => {
    const s = withActive(withOrder(withHolders(game(), 3, 3), [4, 2, 3, 1]), 3);
    expect(holders(eliminate(s, 9, [out(3)]))).toEqual([1, 1]);
    const t = withActive(withOrder(withHolders(game(), 1, null), [3, 2, 1, 4]), 1);
    expect(holders(eliminate(t, 9, [out(1)]))).toEqual([4, null]);
  });

  it('gaps in the seat ids do not matter, only the positions of the order', () => {
    const s = run(
      activeGame(4),
      make('player_conceded', { seat: 2 }),
      make('monarch_set', { seat: 1 }),
    );
    expect(s.monarch).toBe(1);
    const gapped: GameState = {
      ...s,
      seats: s.seats.filter((x) => x.seat !== 2),
      order: [1, 3, 4],
    };
    expect(holders(eliminate(withActive(gapped, 1), 9, [out(1)]))).toEqual([3, null]);
  });

  it('with the active seat eliminated by the same event, the next seat after it that survives receives it', () => {
    // several seats at once (S-6.1.7 will produce this with group_drain): seats 1 (active), 2 and 3
    // go out together, and seat 2 holds the monarch, seat 3 the initiative.
    const s = withHolders(game(), 2, 3);
    expect(holders(eliminate(s, 9, [out(1), out(2), out(3)]))).toEqual([4, 4]);
    // the holder out together with the active seat, the order starts at the active seat
    const t = withActive(withHolders(game(), 3, null), 2);
    expect(holders(eliminate(t, 9, [out(2), out(3)]))).toEqual([4, null]);
    // wrapping: 3 and 4 and the active 2 go out, 1 is the only survivor
    const u = withActive(withHolders(game(), 4, 3), 2);
    expect(holders(eliminate(u, 9, [out(2), out(3), out(4)]))).toEqual([1, 1]);
  });

  it('is independent for the two keys: one holder eliminated, the other not', () => {
    const s = withHolders(game(), 3, 4);
    expect(holders(eliminate(s, 9, [out(3)]))).toEqual([1, 4]);
    expect(holders(eliminate(s, 9, [out(4)]))).toEqual([3, 1]);
  });

  it('a holder that this call did not eliminate keeps it, even if it is out already or not at the table', () => {
    const outEarlier = eliminated(withHolders(game(), 2, 3), 2);
    expect(holders(eliminate(outEarlier, 9, [out(4)]))).toEqual([2, 3]);
    const notThere = withHolders(game(), 7, 6);
    expect(holders(eliminate(notThere, 9, [out(4)]))).toEqual([7, 6]);
  });

  it('a skipped entry (a seat already out, or not at the table, or named twice) hands nothing off', () => {
    const s = eliminated(withHolders(game(), 2, null), 2);
    expect(holders(eliminate(s, 9, [out(2)]))).toEqual([2, null]);
    expect(holders(eliminate(withHolders(game(), 2, null), 9, [out(7)]))).toEqual([2, null]);
  });

  it('with nobody left standing both become null, whoever held them', () => {
    const s = withHolders(game(), 2, 4);
    const next = eliminate(s, 9, [out(1), out(2), out(3), out(4)]);
    expect(holders(next)).toEqual([null, null]);
    expect(next.no_winner).toBe(true);
    const last = eliminated(withHolders(game(), 3, 3), 1, 2, 4);
    expect(holders(eliminate(withActive(last, 3), 9, [out(3)]))).toEqual([null, null]);
  });

  it('with nobody left standing the answer is null even if order names a seat that is not at the table', () => {
    // Not reachable by events; the rule must not depend on what `order` holds (4.5.2 step 4).
    const two = withOrder(withHolders(activeGame(2), 2, 1), [1, 2, 7]);
    expect(holders(eliminate(two, 9, [out(1), out(2)]))).toEqual([null, null]);
  });

  it('a holder that is the only survivor when another seat goes keeps it (no draw)', () => {
    const s = eliminated(withHolders(game(), 3, null), 1, 2);
    expect(holders(eliminate(s, 9, [out(4)]))).toEqual([3, null]);
  });

  it('works the same in the ending phase', () => {
    const s = inPhase(withHolders(game(), 3, null), 'ending');
    expect(holders(eliminate(s, 9, [out(3)]))).toEqual([1, null]);
    const t = inPhase(withActive(withHolders(game(), 3, 3), 3), 'ending');
    expect(holders(eliminate(t, 9, [out(3)]))).toEqual([4, 4]);
  });

  it('with no active seat (not reachable by events) the scan starts at the head of the order', () => {
    const s = withActive(withHolders(game(), 2, 1), null);
    expect(holders(eliminate(s, 9, [out(2), out(1)]))).toEqual([3, 3]);
    const t = withActive(withOrder(withHolders(game(), 2, null), [4, 3, 2, 1]), null);
    expect(holders(eliminate(t, 9, [out(2)]))).toEqual([4, null]);
  });

  it('does not change anything else: only the elimination fields, no_winner, monarch and initiative', () => {
    const before = withHolders(game(), 3, 3);
    const next = eliminate(before, 9, [out(3)]);
    expect({ ...next, seats: [], monarch: null, initiative: null }).toEqual({
      ...before,
      seats: [],
      monarch: null,
      initiative: null,
    });
    expect([next.active_seat, next.turn, next.round, next.last_seq]).toEqual([
      before.active_seat,
      before.turn,
      before.round,
      before.last_seq,
    ]);
  });

  it('does not mutate the input state', () => {
    const before = withHolders(game(), 3, 3);
    const copy = canon(before);
    eliminate(before, 9, [out(3)]);
    expect(canon(before)).toBe(copy);
  });

  it('a state with no holder is returned with null holders unchanged', () => {
    const next = eliminate(game(), 9, [out(3)]);
    expect(holders(next)).toEqual([null, null]);
  });
});

describe('the hand-off through events', () => {
  it('criterion 4: seat 3 is the monarch and seat 1 the active player; seat 3 is eliminated: seat 1 is the monarch', () => {
    const s = after(
      after(activeGame(4), make('monarch_set', { seat: 3 })),
      make('player_conceded', { seat: 3 }),
    );
    expect(s.monarch).toBe(1);
    expect(s.active_seat).toBe(1);
  });

  it('criterion 4: the eliminated monarch was the active player: the next seat in turn order is the monarch', () => {
    const s = run(
      activeGame(4, 3),
      make('monarch_set', { seat: 3 }),
      make('initiative_set', { seat: 3 }),
      make('life_changed', { seat: 3, delta: -40 }),
    );
    expect([s.monarch, s.initiative, s.active_seat]).toEqual([4, 4, 3]);
  });

  it('the monarch who is active when turn_passed made it so goes to the next seat, not the previous active one', () => {
    const log = [
      make('monarch_set', { seat: 2 }),
      make('turn_passed'),
      make('player_conceded', { seat: 2 }),
    ];
    const state = run(activeGame(4), ...log);
    expect([state.active_seat, state.monarch]).toEqual([2, 3]);
    expect(canon(run(activeGame(4), ...log))).toBe(canon(state));
  });
});
