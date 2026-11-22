import { describe, expect, it } from 'vitest';
import { canonicalJson } from './canonical-json';
import { apply } from './reducer';
import { initialState } from './state';
import type { EventEnvelope } from './types';

/**
 * Review rule (CONTRACT.md section 2 "Well-formed strings", 6.2 steps 3 to 7): every envelope
 * string is free of lone surrogates (`invalid_event`; a `type` with one is `unknown_event_type`),
 * and a payload with one is `invalid_payload`. Shared vectors `reject-*lone-surrogate*` and
 * `reject-precedence-lone-surrogate-*` run the same cases on both sides; these are the unit form.
 */

const GAME_ID = '018f0000-0000-7000-8000-000000000001';
const HIGH = String.fromCharCode(0xd800);
const LOW = String.fromCharCode(0xdc00);
const PAIR = HIGH + LOW; // U+10000, a valid pair

function event(overrides: Record<string, unknown> = {}): EventEnvelope {
  return {
    event_id: '00000000-0000-4000-8000-000000000001',
    game_id: GAME_ID,
    type: 'turn_passed',
    payload: {},
    actor: { kind: 'player', player_id: 'p1' },
    on_behalf_of_seat: null,
    v: 1,
    ...overrides,
  } as unknown as EventEnvelope;
}

const lobby = () => initialState(GAME_ID);
const code = (e: unknown): string => {
  const r = apply(lobby(), e as EventEnvelope);
  return r.ok ? 'ok' : r.error.code;
};

const BAD_STRINGS: readonly [string, string][] = [
  ['a lone high surrogate', 'a' + HIGH],
  ['a lone low surrogate', LOW + 'a'],
  ['a low surrogate then a high surrogate', LOW + HIGH],
  ['two high surrogates', HIGH + HIGH],
  ['a high surrogate before a BMP character', HIGH + 'x'],
  ['only the last unit of the highest pair', String.fromCharCode(0xdbff)],
  ['only the last low surrogate', String.fromCharCode(0xdfff)],
];

describe('envelope strings with a lone surrogate are invalid_event', () => {
  describe.each(BAD_STRINGS)('%s', (_name, bad) => {
    it('in game_id', () => expect(code(event({ game_id: bad }))).toBe('invalid_event'));
    it('in ts', () => expect(code(event({ ts: bad }))).toBe('invalid_event'));
    it('in a player id', () =>
      expect(code(event({ actor: { kind: 'player', player_id: bad } }))).toBe('invalid_event'));
    it('in event_id (it can never match the UUID format)', () =>
      expect(code(event({ event_id: bad }))).toBe('invalid_event'));
    it('in type: the type is unknown, so unknown_event_type', () =>
      expect(code(event({ type: bad }))).toBe('unknown_event_type'));
    it('in the payload (a value): invalid_payload', () =>
      expect(code(event({ payload: { k: bad } }))).toBe('invalid_payload'));
    it('in the payload (a key): invalid_payload', () =>
      expect(code(event({ payload: { [bad]: 1 } }))).toBe('invalid_payload'));
  });

  it('accepts a valid surrogate pair in every envelope string and in the payload', () => {
    const gameId = 'g-' + PAIR;
    const state = initialState(gameId);
    const result = apply(
      state,
      event({
        game_id: gameId,
        ts: 't' + PAIR,
        actor: { kind: 'player', player_id: 'p' + PAIR },
        payload: { ['k' + PAIR]: [PAIR] },
      }),
    );
    expect(result).toEqual({ ok: true, state: { ...state, last_seq: 1 } });
  });

  it('treats the text of a surrogate escape as ordinary text', () => {
    const text = String.fromCharCode(92) + 'ud800';
    expect(code(event({ ts: text, payload: { a: text } }))).toBe('ok');
  });
});

describe('precedence of the well-formed-string rule (6.2)', () => {
  it('step 1 and 2 come first: a bad or unsupported v beats a lone surrogate', () => {
    expect(code(event({ v: 2, game_id: HIGH }))).toBe('unsupported_version');
    expect(code(event({ v: true, game_id: HIGH }))).toBe('invalid_event');
  });

  it('step 3 comes before: an unknown type beats a lone surrogate ts', () => {
    expect(code(event({ type: 'nope', ts: HIGH }))).toBe('unknown_event_type');
    expect(code(event({ type: 'nope' + HIGH, extra: 1 }))).toBe('unknown_event_type');
  });

  it('step 3 comes before: a type that is not a string is invalid_event', () => {
    expect(code(event({ type: 5, ts: HIGH }))).toBe('invalid_event');
  });

  it('step 4 comes before step 5: a lone surrogate envelope string beats a bad payload', () => {
    expect(code(event({ ts: HIGH, payload: { a: 1.5 } }))).toBe('invalid_event');
    expect(code(event({ ts: HIGH, payload: { a: HIGH } }))).toBe('invalid_event');
    expect(
      code(event({ actor: { kind: 'player', player_id: HIGH }, payload: { a: 2 ** 53 } })),
    ).toBe('invalid_event');
  });

  it('step 4 comes before steps 6 and 7: it beats game_mismatch and out_of_order', () => {
    expect(code(event({ game_id: 'other' + HIGH }))).toBe('invalid_event');
    expect(code(event({ game_id: 'other-game', ts: HIGH }))).toBe('invalid_event');
    expect(code(event({ seq: 5, ts: HIGH }))).toBe('invalid_event');
  });

  it('step 5 comes before steps 6 and 7: a payload lone surrogate beats them', () => {
    expect(code(event({ game_id: 'other-game', payload: { a: HIGH } }))).toBe('invalid_payload');
    expect(code(event({ seq: 5, payload: { a: LOW } }))).toBe('invalid_payload');
  });

  it('a rejected event leaves the state unchanged', () => {
    const state = lobby();
    const before = canonicalJson(state);
    expect(apply(state, event({ ts: HIGH })).ok).toBe(false);
    expect(canonicalJson(state)).toBe(before);
  });
});
