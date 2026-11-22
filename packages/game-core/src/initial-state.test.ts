import { readdirSync, readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { canonicalJson } from './canonical-json';
import { initialState } from './state';
import { SETTING_DEFAULTS } from './types';
import type { GameSettings } from './types';

/**
 * CONTRACT.md 3.5 and 6.1, TypeScript side: `initialState(game_id, settings?)`. The Python twin is
 * `apps/api/app/games/tests/test_initial_state_parity.py`; the same inputs are in the shared
 * `initial-state-*` vectors, which both runners execute and compare as canonical text. The class of
 * the thrown error is language-specific (a TypeError here), so the throwing cases are paired
 * tests, not vectors.
 */

const HIGH = String.fromCharCode(0xd800);
const LOW = String.fromCharCode(0xdc00);
const dir = new URL('../vectors/', import.meta.url);

function vector(name: string): {
  game_id: string;
  settings: Record<string, unknown>;
  expect: { state: unknown };
} {
  return JSON.parse(readFileSync(new URL(`${name}.json`, dir), 'utf8')) as never;
}
const build = (id: string, settings?: Record<string, unknown>) =>
  initialState(id, settings as Partial<GameSettings> | undefined);

describe('initialState: partial settings and defaults', () => {
  it('uses the four defaults of 3.2 when settings are omitted or empty', () => {
    const expected = {
      starting_life: 40,
      commander_damage_threshold: 21,
      poison_threshold: 10,
      trust_mode: 'casual',
    };
    expect(initialState('g').settings).toEqual(expected);
    expect(initialState('g', {}).settings).toEqual(expected);
    expect(SETTING_DEFAULTS).toEqual(expected);
  });

  it.each([
    [
      { starting_life: 20 },
      {
        starting_life: 20,
        commander_damage_threshold: 21,
        poison_threshold: 10,
        trust_mode: 'casual',
      },
    ],
    [
      { trust_mode: 'strict' },
      {
        starting_life: 40,
        commander_damage_threshold: 21,
        poison_threshold: 10,
        trust_mode: 'strict',
      },
    ],
    [
      { poison_threshold: 1, commander_damage_threshold: 99 },
      {
        starting_life: 40,
        commander_damage_threshold: 99,
        poison_threshold: 1,
        trust_mode: 'casual',
      },
    ],
  ])('fills the missing keys of %j with defaults', (given, expected) => {
    expect(build('g', given).settings).toEqual(expected);
  });

  it('stores values as given, validating neither types nor ranges', () => {
    const given = {
      starting_life: 5000,
      commander_damage_threshold: 0,
      poison_threshold: 100,
      trust_mode: 'banana',
    };
    expect(build('g', given).settings).toEqual(given);
    const odd = {
      starting_life: 'lots',
      commander_damage_threshold: null,
      poison_threshold: -5,
      trust_mode: 7,
    };
    expect(build('g', odd).settings).toEqual(odd);
  });

  it('copies the given settings: changing the object later does not change the state', () => {
    const given = { starting_life: 20 };
    const state = build('g', given);
    given.starting_life = 99;
    expect(state.settings.starting_life).toBe(20);
  });

  it('is independent of the order of the setting keys', () => {
    const a = canonicalJson(build('g', { starting_life: 1, trust_mode: 'strict' }));
    const b = canonicalJson(build('g', { trust_mode: 'strict', starting_life: 1 }));
    expect(a).toBe(b);
  });
});

describe('initialState throws (not a reducer error code) on bad input', () => {
  it.each([
    ['an empty game_id', ''],
    ['a game_id with a lone high surrogate', 'g' + HIGH],
    ['a game_id with a lone low surrogate', LOW + 'g'],
    ['a game_id with reversed surrogates', LOW + HIGH],
    ['a number', 5],
    ['null', null],
    ['undefined', undefined],
    ['an object', {}],
  ])('rejects %s as game_id', (_name, id) => {
    expect(() => initialState(id as string)).toThrow(TypeError);
  });

  it('accepts a game_id with a valid surrogate pair and with any non-empty text', () => {
    expect(initialState('g-' + HIGH + LOW).game_id).toBe('g-' + HIGH + LOW);
    expect(initialState(' ').game_id).toBe(' ');
    expect(initialState('\u0000').game_id).toBe('\u0000');
  });

  it.each([
    ['an unknown key', { starting_lives: 20 }],
    ['an unknown key next to valid ones', { starting_life: 20, extra: 1 }],
    ['a key that only differs in case', { Starting_Life: 20 }],
    ['an inherited-looking key', { constructor: 1 }],
    ['a __proto__ own key', JSON.parse('{"__proto__": 1}') as object],
  ])('rejects %s in settings', (_name, settings) => {
    expect(() => build('g', settings as Record<string, unknown>)).toThrow(TypeError);
  });

  it.each([
    ['a string', 'casual'],
    ['an array', []],
    ['a number', 3],
    ['null', null],
  ])('rejects settings that are %s', (_name, settings) => {
    expect(() => initialState('g', settings as never)).toThrow(TypeError);
  });
});

describe('initialState matches the shared initial-state vectors', () => {
  const names = readdirSync(dir)
    .filter((f) => f.startsWith('initial-state') && f.endsWith('.json'))
    .map((f) => f.slice(0, -5));

  it('finds the vectors', () => {
    expect(names).toEqual(
      expect.arrayContaining([
        'initial-state',
        'initial-state-default-settings',
        'initial-state-out-of-range-settings',
        'initial-state-partial-settings',
        'initial-state-partial-settings-trust-mode-only',
        'initial-state-empty-settings',
        'initial-state-unvalidated-setting-types',
        'initial-state-astral-game-id',
      ]),
    );
  });

  it.each(names)('%s: initialState gives exactly the expected canonical state', (name) => {
    const v = vector(name);
    expect(canonicalJson(build(v.game_id, v.settings))).toBe(canonicalJson(v.expect.state));
  });
});
