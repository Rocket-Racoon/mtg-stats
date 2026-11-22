import { readFileSync } from 'node:fs';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { canonicalJson, canonicalJsonBytes } from './canonical-json';
import { apply, reduce, replay } from './reducer';
import { initialState } from './state';
import { activeGame, CARRIER_TYPE, NO_OP_TYPES, S_6_1_6_TYPES, seated } from './lobby.test.helpers';
import { EVENT_TYPES, SETTING_DEFAULTS } from './types';
import type { EventEnvelope, GameState } from './types';

/**
 * Acceptance criteria 3 and 4 (S-6.1.1), behavioural: the reducer is pure in practice, and it
 * rejects unknown types and unsupported versions with the codes of CONTRACT.md section 6.2.
 */

const GAME_ID = '018f0000-0000-7000-8000-000000000001';
const PLAYER_ID = '018f0000-0000-7000-8000-0000000000a1';

let counter = 0;
function event(overrides: Record<string, unknown> = {}): EventEnvelope {
  counter += 1;
  return {
    event_id: `00000000-0000-4000-8000-${String(counter).padStart(12, '0')}`,
    game_id: GAME_ID,
    type: CARRIER_TYPE,
    payload: {},
    actor: { kind: 'player', player_id: PLAYER_ID },
    on_behalf_of_seat: null,
    v: 1,
    ...overrides,
  } as unknown as EventEnvelope;
}

const vectorsDir = new URL('../vectors/', import.meta.url);
/** A populated four-seat state; seats cannot be reached through the no-op handlers. */
function populatedState(overrides: Partial<GameState> = {}): GameState {
  const vector = JSON.parse(
    readFileSync(new URL('canonical-json-game-state.json', vectorsDir), 'utf8'),
  ) as { cases: { input: unknown }[] };
  return { ...(vector.cases[0]!.input as GameState), game_id: GAME_ID, ...overrides };
}

function deepFreeze<T>(value: T): T {
  if (typeof value === 'object' && value !== null && !Object.isFrozen(value)) {
    Object.freeze(value);
    for (const child of Object.values(value)) deepFreeze(child);
  }
  return value;
}

const lobby = (): GameState => initialState(GAME_ID, SETTING_DEFAULTS);
const text = (r: ReturnType<typeof apply>): string => canonicalJson(r);

describe('criterion 3: apply reads no clock, randomness or I/O', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  /** Make every clock, randomness and network entry point throw, then run `fn`. */
  function withTraps<T>(fn: () => T): T {
    const boom = (name: string) => () => {
      throw new Error(`forbidden call: ${name}`);
    };
    const RealDate = Date;
    vi.stubGlobal(
      'Date',
      new Proxy(RealDate, {
        construct: boom('new Date'),
        apply: boom('Date()'),
        get(target, prop, receiver) {
          if (prop === 'now') return boom('Date.now');
          return Reflect.get(target, prop, receiver) as unknown;
        },
      }),
    );
    vi.spyOn(Math, 'random').mockImplementation(boom('Math.random'));
    vi.spyOn(performance, 'now').mockImplementation(boom('performance.now'));
    if (typeof globalThis.crypto?.getRandomValues === 'function') {
      vi.spyOn(globalThis.crypto, 'getRandomValues').mockImplementation(boom('getRandomValues'));
    }
    if (typeof globalThis.crypto?.randomUUID === 'function') {
      vi.spyOn(globalThis.crypto, 'randomUUID').mockImplementation(boom('randomUUID'));
    }
    if (typeof globalThis.fetch === 'function') {
      vi.spyOn(globalThis, 'fetch').mockImplementation(boom('fetch'));
    }
    vi.spyOn(globalThis, 'setTimeout').mockImplementation(boom('setTimeout'));
    vi.spyOn(globalThis, 'setInterval').mockImplementation(boom('setInterval'));
    try {
      return fn();
    } finally {
      vi.restoreAllMocks();
      vi.unstubAllGlobals();
    }
  }

  it('has working traps', () => {
    expect(() => withTraps(() => Date.now())).toThrow(/forbidden call/);
    expect(() => withTraps(() => new Date())).toThrow(/forbidden call/);
    expect(() => withTraps(() => Math.random())).toThrow(/forbidden call/);
    expect(() => withTraps(() => performance.now())).toThrow(/forbidden call/);
  });

  it.each(NO_OP_TYPES)('accepts a %s event with every clock and random source trapped', (type) => {
    const state = populatedState();
    const e = event({ type, ts: '2026-11-21T12:00:00.000Z' });
    const plain = apply(state, e);
    const trapped = withTraps(() => apply(state, e));
    expect(trapped.ok).toBe(true);
    expect(text(trapped)).toBe(text(plain));
  });

  /** A state and a valid payload for each S-6.1.6 type, so each handler runs under the traps. */
  const handled: Record<
    (typeof S_6_1_6_TYPES)[number],
    () => [GameState, Record<string, unknown>]
  > = {
    game_created: () => [lobby(), { settings: { ...SETTING_DEFAULTS, starting_life: 30 } }],
    settings_changed: () => [lobby(), { trust_mode: 'strict' }],
    seat_joined: () => [lobby(), { seat: 1, player_id: null, name: 'Ana' }],
    seat_left: () => [seated(2), { seat: 2 }],
    seat_moved: () => [seated(3), { from: 1, to: 3 }],
    deck_assigned: () => [
      seated(2),
      { seat: 1, deck_version_id: 'd', commanders: [{ card_id: null, name: 'C' }] },
    ],
    commanders_set: () => [seated(2), { seat: 1, commanders: [{ card_id: 'c', name: 'C' }] }],
    game_started: () => [seated(3), { first_seat: 3, started_at: 'any text' }],
    turn_passed: () => [activeGame(3), {}],
  };

  it.each(S_6_1_6_TYPES)(
    'runs the %s handler with every clock and random source trapped',
    (type) => {
      const [state, payload] = handled[type]();
      const e = event({ type, payload, ts: '2026-11-21T12:00:00.000Z' });
      const plain = apply(state, e);
      const trapped = withTraps(() => apply(state, e));
      expect(trapped.ok).toBe(true);
      expect(text(trapped)).toBe(text(plain));
      expect(trapped.ok && trapped.state.last_seq).toBe(state.last_seq + 1);
    },
  );

  it('rejects with every clock and random source trapped, and returns the same rejection', () => {
    const bad = event({ type: 'nope' });
    const trapped = withTraps(() => apply(lobby(), bad));
    expect(trapped).toEqual({ ok: false, error: { code: 'unknown_event_type' } });
    expect(withTraps(() => replay(lobby(), [event(), bad]))).toEqual({
      ok: false,
      index: 1,
      error: { code: 'unknown_event_type' },
    });
  });

  it('serializes canonical JSON with every clock and random source trapped', () => {
    const state = populatedState();
    expect(withTraps(() => canonicalJson(state))).toBe(canonicalJson(state));
  });

  it('gives byte-identical canonical output for the same state and event, every time', () => {
    const state = populatedState();
    const e = event({ type: 'life_changed', payload: { seat: 1, delta: -3 } });
    const first = canonicalJsonBytes(apply(state, e));
    for (let i = 0; i < 50; i++) {
      expect(Buffer.from(canonicalJsonBytes(apply(state, e))).equals(Buffer.from(first))).toBe(
        true,
      );
    }
  });

  it('gives the same result for the same event written with its keys in another order', () => {
    const state = lobby();
    const a = event({ payload: { x: 1, y: 2 } });
    const b = Object.fromEntries(Object.entries(a).reverse()) as unknown as EventEnvelope;
    (b as unknown as { payload: unknown }).payload = { y: 2, x: 1 };
    expect(text(apply(state, b))).toBe(text(apply(state, a)));
  });

  it('never reads ts: any string, or none, gives the same state', () => {
    const state = populatedState();
    const results = [
      undefined,
      '2026-11-21T12:00:00.000Z',
      '1970-01-01T00:00:00.000Z',
      '9999-12-31T23:59:59.999Z',
      'yesterday',
      '',
    ].map((ts) => text(apply(state, event(ts === undefined ? {} : { ts }))));
    expect(new Set(results).size).toBe(1);
  });

  it('does not mutate a deeply frozen state or event, on accept or on reject', () => {
    const state = deepFreeze(populatedState());
    const before = canonicalJson(state);
    const good = deepFreeze(event({ payload: { nested: { list: [1, 2, { k: 'v' }] } } }));
    const goodBefore = canonicalJson(good);
    expect(apply(state, good).ok).toBe(true);
    const bad = deepFreeze(event({ v: 2 }));
    expect(apply(state, bad)).toEqual({ ok: false, error: { code: 'unsupported_version' } });
    expect(canonicalJson(state)).toBe(before);
    expect(canonicalJson(good)).toBe(goodBefore);
  });

  it('returns a new state and leaves the input state unchanged', () => {
    const state = populatedState();
    const before = canonicalJson(state);
    const result = apply(state, event());
    expect(result.ok && result.state).not.toBe(state);
    expect(state.last_seq).toBe(41);
    expect(canonicalJson(state)).toBe(before);
  });

  it('takes the start time and the first seat only from the payload', () => {
    const state = seated(3);
    const started = (payload: Record<string, unknown>) =>
      apply(state, event({ type: 'game_started', payload }));
    const result = started({ first_seat: 3, started_at: '2026-11-21T12:00:00.000Z' });
    expect(result.ok && result.state.started_at).toBe('2026-11-21T12:00:00.000Z');
    expect(result.ok && result.state.active_seat).toBe(3);
    // The same payload always gives the same state; another payload gives that payload's values.
    expect(text(started({ first_seat: 3, started_at: '2026-11-21T12:00:00.000Z' }))).toBe(
      text(result),
    );
    const other = started({ first_seat: 1, started_at: 'whenever' });
    expect(other.ok && [other.state.started_at, other.state.active_seat]).toEqual(['whenever', 1]);
  });
});

describe('every registered event type whose handler is still a no-op is accepted as one', () => {
  it('has the 29 types of the registry, 12 of them with handlers (S-6.1.6, S-6.1.3)', () => {
    expect(EVENT_TYPES).toHaveLength(29);
    expect(NO_OP_TYPES).toHaveLength(17);
  });

  it.each(NO_OP_TYPES)('%s from the initial state only advances last_seq', (type) => {
    const state = lobby();
    expect(apply(state, event({ type }))).toEqual({ ok: true, state: { ...state, last_seq: 1 } });
  });

  it.each(NO_OP_TYPES)('%s with seq last_seq + 1 from a populated state', (type) => {
    const state = populatedState();
    expect(apply(state, event({ type, seq: 42 }))).toEqual({
      ok: true,
      state: { ...state, last_seq: 42 },
    });
  });

  it.each(['lobby', 'active', 'ending', 'finished', 'abandoned'] as const)(
    'an event is accepted in a %s game (game_closed is handler-level, not produced yet)',
    (phase) => {
      const state = populatedState({
        phase,
        resume_phase: phase === 'abandoned' ? 'active' : null,
      });
      expect(apply(state, event())).toEqual({
        ok: true,
        state: { ...state, last_seq: 42 },
      });
    },
  );

  it('reduce is the same function as apply', () => {
    expect(reduce).toBe(apply);
  });

  it('replay folds apply and reports the first rejection', () => {
    expect(replay(lobby(), [event(), event(), event()])).toEqual({
      ok: true,
      state: { ...lobby(), last_seq: 3 },
    });
    expect(replay(lobby(), [event(), event({ seq: 7 }), event({ v: 3 })])).toEqual({
      ok: false,
      index: 1,
      error: { code: 'out_of_order' },
    });
  });
});

describe('criterion 4: unknown type and unsupported version', () => {
  const code = (e: unknown, state = lobby()): string => {
    const r = apply(state, e as EventEnvelope);
    return r.ok ? 'ok' : r.error.code;
  };

  it.each([
    ['Turn_Passed'],
    ['TURN_PASSED'],
    ['turn_passed '],
    [' turn_passed'],
    ['turn_passed\n'],
    ['turn-passed'],
    ['turn_pass'],
    ['turn_passed\u0000'],
    ['turn_pаssed'],
    [''],
    ['no_such_event'],
    ['__proto__'],
    ['constructor'],
    ['toString'],
    ['hasOwnProperty'],
  ])('rejects the type %j with unknown_event_type', (type) => {
    expect(code(event({ type }))).toBe('unknown_event_type');
  });

  it.each([[0], [2], [-1], [100], [9007199254740991], [-9007199254740991]])(
    'rejects v = %d with unsupported_version',
    (v) => {
      expect(code(event({ v }))).toBe('unsupported_version');
    },
  );

  it.each([
    ['2^53', 9007199254740992],
    ['-(2^53)', -9007199254740992],
    ['1e21', 1e21],
    ['a fraction', 1.5],
    ['NaN', Number.NaN],
    ['Infinity', Number.POSITIVE_INFINITY],
    ['true', true],
    ['false', false],
    ['null', null],
    ['a string', '1'],
    ['an array', [1]],
    ['an object', { v: 1 }],
  ])('rejects v = %s (not an integer in the safe range) with invalid_event', (_n, v) => {
    expect(code(event({ v }))).toBe('invalid_event');
  });

  it('rejects a missing v and a non-object event with invalid_event', () => {
    const e = event() as unknown as Record<string, unknown>;
    delete e.v;
    expect(code(e)).toBe('invalid_event');
    for (const bad of [null, undefined, 1, 'x', [], true]) expect(code(bad)).toBe('invalid_event');
  });

  it('rejects a type that is not a string with invalid_event', () => {
    for (const type of [5, null, true, { a: 1 }, ['turn_passed'], undefined]) {
      expect(code(event({ type }))).toBe('invalid_event');
    }
  });

  it('checks v before type: both wrong gives unsupported_version', () => {
    expect(code(event({ v: 0, type: 'no_such_event' }))).toBe('unsupported_version');
    expect(code(event({ v: 2, type: 7 }))).toBe('unsupported_version');
  });

  it('checks v and type before the rest of the envelope and the payload', () => {
    const malformed = { v: 2, type: 'nope', payload: { a: 1.5 }, actor: 'x' };
    expect(code(malformed)).toBe('unsupported_version');
    expect(code({ ...malformed, v: 1 })).toBe('unknown_event_type');
  });

  it('rejects a rejected event without changing the caller state', () => {
    const state = populatedState();
    const before = canonicalJson(state);
    expect(code(event({ v: 2 }), state)).toBe('unsupported_version');
    expect(canonicalJson(state)).toBe(before);
  });
});

describe('initialState (contract 3.5 and 3.2)', () => {
  it('has the four settings, with the contract defaults', () => {
    expect(initialState(GAME_ID).settings).toEqual({
      starting_life: 40,
      commander_damage_threshold: 21,
      poison_threshold: 10,
      trust_mode: 'casual',
    });
  });

  it('stores out-of-range settings unchanged', () => {
    const settings = {
      starting_life: 5000,
      commander_damage_threshold: 0,
      poison_threshold: 100,
      trust_mode: 'banana',
    } as unknown as GameState['settings'];
    expect(initialState(GAME_ID, settings).settings).toEqual(settings);
  });

  it('returns an independent object on every call', () => {
    const a = initialState(GAME_ID);
    a.seats.push({} as never);
    a.voided.push(1);
    a.settings.starting_life = 1;
    (a.commander_damage as Record<string, unknown>).x = {};
    expect(canonicalJson(initialState(GAME_ID))).toBe(canonicalJson(lobby()));
  });
});
