import { describe, expect, it } from 'vitest';
import { apply, reduce, replay } from './reducer';
import { initialState } from './state';
import { EVENT_TYPES, type EventEnvelope, type ReducerErrorCode } from './types';

const GAME_ID = '018f0000-0000-7000-8000-000000000001';

function event(over: Record<string, unknown> = {}): EventEnvelope {
  return {
    event_id: '00000000-0000-4000-8000-000000000001',
    game_id: GAME_ID,
    type: 'turn_passed',
    payload: {},
    actor: { kind: 'player', player_id: 'p1' },
    on_behalf_of_seat: null,
    v: 1,
    ...over,
  } as EventEnvelope;
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
    ['unknown envelope key', event({ extra: 1 })],
    ['on_behalf_of_seat 9', event({ on_behalf_of_seat: 9 })],
  ])('rejects a malformed envelope: %s', (_name, e) => {
    expect(codeFor(e)).toBe('invalid_event');
  });

  it('rejects another game and out-of-order seq', () => {
    expect(codeFor(event({ game_id: 'other' }))).toBe('game_mismatch');
    expect(codeFor(event({ seq: 2 }))).toBe('out_of_order');
  });

  it('accepts every registered type through its no-op handler and advances last_seq', () => {
    for (const type of EVENT_TYPES) {
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

describe('replay', () => {
  it('folds events and reports the index of the first rejection', () => {
    const s0 = initialState(GAME_ID);
    const ok = replay(s0, [event(), event()]);
    expect(ok.ok && ok.state.last_seq).toBe(2);
    const bad = replay(s0, [event(), event({ type: 'nope' }), event()]);
    expect(bad).toEqual({ ok: false, index: 1, error: { code: 'unknown_event_type' } });
  });
});
