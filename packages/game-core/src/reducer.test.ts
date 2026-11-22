import { describe, expect, it } from 'vitest';
import { apply, reduce, replay } from './reducer';
import { initialState } from './state';
import { CARRIER_TYPE, NO_OP_TYPES } from './lobby.test.helpers';
import type { EventEnvelope, ReducerErrorCode } from './types';

const GAME_ID = '018f0000-0000-7000-8000-000000000001';

function event(over: Record<string, unknown> = {}): EventEnvelope {
  return {
    event_id: '00000000-0000-4000-8000-000000000001',
    game_id: GAME_ID,
    type: CARRIER_TYPE,
    payload: {},
    actor: { kind: 'player', player_id: 'p1' },
    on_behalf_of_seat: null,
    v: 1,
    ...over,
  } as EventEnvelope;
}

/** A payload object chain with `count` nested objects, the payload itself included. */
function deep(count: number): Record<string, unknown> {
  let value: Record<string, unknown> = {};
  for (let i = 1; i < count; i++) value = { a: value };
  return value;
}

const codeFor = (e: unknown): ReducerErrorCode | 'ok' => {
  const r = apply(initialState(GAME_ID), e as EventEnvelope);
  return r.ok ? 'ok' : r.error.code;
};

describe('apply', () => {
  it('rejects an unknown type', () => {
    expect(codeFor(event({ type: 'nope' }))).toBe('unknown_event_type');
  });

  it('rejects an unsupported version, checked before the type', () => {
    expect(codeFor(event({ v: 2 }))).toBe('unsupported_version');
    expect(codeFor(event({ v: 0, type: 'nope' }))).toBe('unsupported_version');
  });

  it.each([
    ['null event', null],
    ['missing v', event({ v: undefined })],
    ['fractional v', event({ v: 1.5 })],
    ['non-string type', event({ type: 3 })],
    ['bad event_id', event({ event_id: 'abc' })],
    ['uppercase event_id', event({ event_id: '00000000-0000-4000-8000-00000000000A' })],
    ['uuid v7 event_id', event({ event_id: '018f0000-0000-7000-8000-000000000001' })],
    ['empty game_id', event({ game_id: '' })],
    ['seq 0', event({ seq: 0 })],
    ['seq null', event({ seq: null })],
    ['ts number', event({ ts: 5 })],
    ['payload array', event({ payload: [] })],
    ['missing actor', event({ actor: undefined })],
    ['actor kind', event({ actor: { kind: 'bot', player_id: null } })],
    ['on_behalf_of_seat missing', event({ on_behalf_of_seat: undefined })],
    ['v true', event({ v: true })],
    ['v null', event({ v: null })],
    ['seq negative', event({ seq: -3 })],
    ['ts null', event({ ts: null })],
    ['player actor with empty id', event({ actor: { kind: 'player', player_id: '' } })],
    ['player actor with null id', event({ actor: { kind: 'player', player_id: null } })],
    ['system actor with an id', event({ actor: { kind: 'system', player_id: 'p1' } })],
    ['actor with an extra key', event({ actor: { kind: 'system', player_id: null, x: 1 } })],
    ['actor missing player_id', event({ actor: { kind: 'system' } })],
    ['unknown envelope key', event({ extra: 1 })],
    ['on_behalf_of_seat 9', event({ on_behalf_of_seat: 9 })],
  ])('rejects a malformed envelope: %s', (_name, e) => {
    expect(codeFor(e)).toBe('invalid_event');
  });

  it('accepts a system actor with a null id and any ts string', () => {
    const e = event({ actor: { kind: 'system', player_id: null }, ts: 'not a timestamp' });
    expect(codeFor(e)).toBe('ok');
  });

  it.each([
    ['fraction', { seat: 1.5 }],
    ['NaN', { seat: Number.NaN }],
    ['unsafe integer', { seat: 9007199254740992 }],
    ['lone surrogate', { name: String.fromCharCode(0xd800) }],
    ['undefined value', { seat: undefined }],
    ['too deep', deep(66)],
  ])('rejects a non-canonical payload (%s) with invalid_payload', (_name, payload) => {
    expect(codeFor(event({ payload }))).toBe('invalid_payload');
  });

  it('accepts a payload at the nesting limit', () => {
    expect(codeFor(event({ payload: deep(65) }))).toBe('ok');
  });

  it('checks the payload after the envelope and before game_id and seq', () => {
    const bad = { seat: 1.5 };
    expect(codeFor(event({ payload: bad, game_id: 'other', seq: 7 }))).toBe('invalid_payload');
    expect(codeFor(event({ payload: bad, actor: null }))).toBe('invalid_event');
    expect(codeFor(event({ payload: bad, type: 'nope' }))).toBe('unknown_event_type');
  });

  it('does not check game_closed: no-op handlers accept events in any phase', () => {
    for (const phase of ['finished', 'abandoned'] as const) {
      const r = apply({ ...initialState(GAME_ID), phase }, event());
      expect(r.ok).toBe(true);
    }
  });

  it('does not validate settings ranges in initialState', () => {
    expect(initialState(GAME_ID, { starting_life: 0 }).settings.starting_life).toBe(0);
  });

  it('rejects another game and out-of-order seq', () => {
    expect(codeFor(event({ game_id: 'other' }))).toBe('game_mismatch');
    expect(codeFor(event({ seq: 2 }))).toBe('out_of_order');
  });

  it('accepts every type whose handler is still a no-op and advances last_seq only', () => {
    for (const type of NO_OP_TYPES) {
      const state = initialState(GAME_ID);
      const r = apply(state, event({ type }));
      expect(r.ok).toBe(true);
      if (r.ok) expect(r.state).toEqual({ ...state, last_seq: 1 });
    }
  });

  it('uses seq when given and last_seq + 1 when absent', () => {
    const s0 = initialState(GAME_ID);
    const r1 = apply(s0, event({ seq: 1, ts: '2026-01-01T00:00:00.000Z' }));
    expect(r1.ok && r1.state.last_seq).toBe(1);
    if (!r1.ok) return;
    expect(apply(r1.state, event({ seq: 1 })).ok).toBe(false);
    const r2 = apply(r1.state, event());
    expect(r2.ok && r2.state.last_seq).toBe(2);
  });

  it('never mutates its inputs', () => {
    const state = initialState(GAME_ID);
    const e = event();
    const before = JSON.stringify([state, e]);
    apply(state, e);
    expect(JSON.stringify([state, e])).toBe(before);
  });

  it('exports reduce as an alias', () => {
    expect(reduce).toBe(apply);
  });
});

describe('envelope strings must be well formed', () => {
  const lone = String.fromCharCode(0xd800);
  const low = String.fromCharCode(0xdc00);
  const pair = String.fromCharCode(0xd83d, 0xde00);

  it.each([
    ['game_id', { game_id: 'g' + lone }],
    ['game_id (low surrogate)', { game_id: low + 'g' }],
    ['ts', { ts: '2026' + lone }],
    ['actor.player_id', { actor: { kind: 'player', player_id: 'p' + lone } }],
  ])('rejects a lone surrogate in %s with invalid_event', (_name, over) => {
    expect(codeFor(event(over))).toBe('invalid_event');
  });

  it('gives a type with a lone surrogate unknown_event_type (step 3 runs first)', () => {
    expect(codeFor(event({ type: 'turn_passed' + lone }))).toBe('unknown_event_type');
  });

  it('accepts valid surrogate pairs in ts and player_id', () => {
    expect(codeFor(event({ ts: pair, actor: { kind: 'player', player_id: pair } }))).toBe('ok');
  });
});

describe('initialState', () => {
  it('throws on an empty, non-string or ill-formed game_id', () => {
    expect(() => initialState('')).toThrow(TypeError);
    expect(() => initialState(5 as unknown as string)).toThrow(TypeError);
    expect(() => initialState('g' + String.fromCharCode(0xd800))).toThrow(TypeError);
  });

  it('throws on an unknown setting key or a non-object settings', () => {
    expect(() => initialState(GAME_ID, { foo: 1 } as never)).toThrow(TypeError);
    expect(() => initialState(GAME_ID, null as never)).toThrow(TypeError);
    expect(() => initialState(GAME_ID, [] as never)).toThrow(TypeError);
  });

  it('fills missing settings with the defaults and copies values without validating', () => {
    const s = initialState(GAME_ID, { poison_threshold: 12, starting_life: 0 });
    expect(s.settings).toEqual({
      starting_life: 0,
      commander_damage_threshold: 21,
      poison_threshold: 12,
      trust_mode: 'casual',
    });
  });

  it('starts with empty voided and reverted arrays', () => {
    const s = initialState(GAME_ID);
    expect(s.voided).toEqual([]);
    expect(s.reverted).toEqual([]);
  });
});

describe('replay', () => {
  it('folds events and reports the index of the first rejection', () => {
    const s0 = initialState(GAME_ID);
    const ok = replay(s0, [event(), event()]);
    expect(ok.ok && ok.state.last_seq).toBe(2);
    const bad = replay(s0, [event(), event({ type: 'nope' }), event()]);
    expect(bad).toEqual({ ok: false, index: 1, error: { code: 'unknown_event_type' } });
  });
});
