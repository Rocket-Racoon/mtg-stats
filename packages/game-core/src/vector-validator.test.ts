import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { EVENT_TYPES } from './types';
import { loadVectorFiles } from './vector-loader.test.helpers';
import { checkCoverage, exercisedEventTypes, validateVectorText } from './vector-validator';

const GAME = '018f0000-0000-7000-8000-000000000001';
const event = (type: unknown, extra: Record<string, unknown> = {}) => ({
  event_id: '00000000-0000-4000-8000-000000000001',
  type,
  payload: {},
  actor: { kind: 'player', player_id: null },
  on_behalf_of_seat: null,
  v: 1,
  ...extra,
});

function reducer(name: string, patch: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    format: 1,
    kind: 'reducer',
    name,
    description: 'd',
    game_id: GAME,
    settings: {},
    events: [event('turn_passed')],
    expect: { state: {} },
    ...patch,
  };
}

function canonical(name: string, cases: unknown[]): Record<string, unknown> {
  return { format: 1, kind: 'canonical_json', name, description: 'd', cases };
}

function check(name: string, vector: unknown): string[] {
  return validateVectorText(`${name}.json`, JSON.stringify(vector));
}

describe('validateVectorText', () => {
  it('accepts a valid reducer vector and a valid canonical vector', () => {
    expect(check('a', reducer('a'))).toEqual([]);
    expect(
      check(
        'c',
        canonical('c', [
          { name: 'x', input: [1], output: '[1]' },
          { name: 'y', invalid: { kind: 'nan' }, error: 'non_integer' },
          { name: 'z', invalid: { kind: 'float', value: '1e-7' }, error: 'non_integer' },
        ]),
      ),
    ).toEqual([]);
  });

  it('names the file for invalid JSON', () => {
    const [message] = validateVectorText('bad.json', '{"a":');
    expect(message).toMatch(/^bad\.json: invalid JSON: /);
  });

  it('rejects a root that is not an object', () => {
    expect(validateVectorText('a.json', '[]')).toEqual(['a.json: $ must be an object']);
  });

  it('rejects unknown and missing top-level keys', () => {
    const rest = reducer('a');
    delete rest.description;
    expect(check('a', { ...rest, extra: 1 })).toEqual([
      'a.json: description is required',
      'a.json: extra is not a known key',
    ]);
    const stray = { ...canonical('c', [{ name: 'x', input: 1, output: '1' }]), game_id: 'x' };
    expect(check('c', stray)).toEqual(['c.json: game_id is not a known key']);
  });

  it('rejects wrong format, kind and name', () => {
    expect(check('a', reducer('a', { format: 2 }))).toEqual([
      'a.json: format must be the integer 1',
    ]);
    expect(check('a', reducer('a', { kind: 'other' }))).toEqual([
      'a.json: kind must be "reducer" or "canonical_json"',
    ]);
    expect(check('a', reducer('b'))).toEqual(['a.json: name must equal "a"']);
    expect(check('a', reducer('a', { description: '' }))).toEqual([
      'a.json: description must be a non-empty string',
    ]);
  });

  it('checks game_id, settings and events', () => {
    expect(check('a', reducer('a', { game_id: 5 }))).toEqual([
      'a.json: game_id must be a non-empty string',
    ]);
    expect(check('a', reducer('a', { settings: [] }))).toEqual([
      'a.json: settings must be an object',
    ]);
    expect(check('a', reducer('a', { settings: { life: 40 } }))).toEqual([
      'a.json: settings.life is not a known key',
    ]);
    expect(check('a', reducer('a', { events: {} }))).toEqual(['a.json: events must be an array']);
    expect(check('a', reducer('a', { events: [1] }))).toEqual([
      'a.json: events[0] must be an object',
    ]);
  });

  it('requires exactly one of expect.state and expect.error', () => {
    const both = reducer('a', {
      expect: { state: {}, error: { index: 0, code: 'invalid_event' } },
    });
    expect(check('a', both)).toEqual([
      'a.json: expect must have exactly one of "state" or "error"',
    ]);
    expect(check('a', reducer('a', { expect: {} }))).toEqual([
      'a.json: expect must have exactly one of "state" or "error"',
    ]);
    expect(check('a', reducer('a', { expect: { state: 1 } }))).toEqual([
      'a.json: expect.state must be an object',
    ]);
  });

  it('checks expect.error.index and expect.error.code', () => {
    const withError = (error: unknown) => check('a', reducer('a', { expect: { error } }));
    expect(withError({ index: -1, code: 'invalid_event' })).toEqual([
      'a.json: expect.error.index must be a non-negative integer',
    ]);
    expect(withError({ index: 1, code: 'invalid_event' })).toEqual([
      'a.json: expect.error.index must be less than the number of events (1)',
    ]);
    expect(withError({ index: 0, code: 'forbidden' })).toEqual([
      'a.json: expect.error.code must be a known reducer error code',
    ]);
    expect(withError({ code: 'invalid_event' })).toEqual([
      'a.json: expect.error.index is required',
    ]);
    expect(withError({ index: 0, code: 'invalid_event', extra: 1 })).toEqual([
      'a.json: expect.error.extra is not a known key',
    ]);
  });

  it('requires a string type for applied events only', () => {
    const events = [event('turn_passed'), event(5), event(null)];
    expect(check('a', reducer('a', { events }))).toEqual([
      'a.json: events[1].type must be a string',
      'a.json: events[2].type must be a string',
    ]);
    const rejected = reducer('a', {
      events,
      expect: { error: { index: 1, code: 'invalid_event' } },
    });
    expect(check('a', rejected)).toEqual([]);
    const late = reducer('a', { events, expect: { error: { index: 2, code: 'invalid_event' } } });
    expect(check('a', late)).toEqual(['a.json: events[1].type must be a string']);
  });

  it('checks canonical cases', () => {
    const one = (c: unknown) => check('c', canonical('c', [c]));
    expect(check('c', canonical('c', []))).toEqual(['c.json: cases must be a non-empty array']);
    expect(one(1)).toEqual(['c.json: cases[0] must be an object']);
    expect(one({ name: 'x' })).toEqual([
      'c.json: cases[0] must have exactly one of "input" or "invalid"',
    ]);
    expect(one({ name: 'x', input: 1, invalid: {} })).toEqual([
      'c.json: cases[0] must have exactly one of "input" or "invalid"',
    ]);
    expect(one({ name: '', input: 1, output: '1' })).toEqual([
      'c.json: cases[0].name must be a non-empty string',
    ]);
    expect(one({ name: 'x', input: 1 })).toEqual(['c.json: cases[0].output is required']);
    expect(one({ name: 'x', input: 1, output: 1 })).toEqual([
      'c.json: cases[0].output must be a string',
    ]);
    expect(one({ name: 'x', input: 1, output: '1', error: 'non_integer' })).toEqual([
      'c.json: cases[0].error is not a known key',
    ]);
    const dup = canonical('c', [
      { name: 'x', input: 1, output: '1' },
      { name: 'x', input: 2, output: '2' },
    ]);
    expect(check('c', dup)).toEqual(['c.json: cases[1].name duplicates the name of cases[0]']);
  });

  it('checks the invalid case spec', () => {
    const bad = (invalid: unknown, error = 'non_integer') =>
      check('c', canonical('c', [{ name: 'x', invalid, error }]));
    expect(bad({ kind: 'bogus' })).toEqual([
      'c.json: cases[0].invalid.kind must be one of float, nan, infinity, integer, lone_surrogate, deep_array, deep_object',
    ]);
    expect(bad({ kind: 'nan' }, 'nope')).toEqual([
      'c.json: cases[0].error must be a canonical JSON error code',
    ]);
    expect(bad({ kind: 'nan', value: '1' })).toEqual([
      'c.json: cases[0].invalid.value must be absent',
    ]);
    expect(bad({ kind: 'float' })).toEqual(['c.json: cases[0].invalid.value is required']);
    expect(bad({ kind: 'float', value: '1.0' })).toEqual([
      'c.json: cases[0].invalid.value must be decimal text of a non-integer number',
    ]);
    expect(bad({ kind: 'integer', value: '5' })).toEqual([
      'c.json: cases[0].invalid.value must be decimal text of an integer outside the safe range',
    ]);
    expect(bad({ kind: 'lone_surrogate', value: '0041' })).toEqual([
      'c.json: cases[0].invalid.value must be the 4-digit hex code of a surrogate code unit (d800 to dfff)',
    ]);
    expect(bad({ kind: 'deep_array', value: '0' })).toEqual([
      'c.json: cases[0].invalid.value must be decimal text of an integer from 1 to 10000',
    ]);
    expect(bad({ kind: 'deep_object', value: '99999999' })).toEqual([
      'c.json: cases[0].invalid.value must be decimal text of an integer from 1 to 10000',
    ]);
  });

  const reducerText = (settings: string, events: string, expectText: string) =>
    `{"format":1,"kind":"reducer","name":"a","description":"d","game_id":"g","settings":${settings},"events":${events},"expect":${expectText}}`;

  it('rejects integral float tokens such as 1.0 and 1e2 anywhere', () => {
    const text = reducerText(
      '{"starting_life":40.0}',
      '[{"type":"turn_passed","v":1.0,"seq":1e2}]',
      '{"state":{"last_seq":1.0}}',
    );
    expect(validateVectorText('a.json', text)).toEqual([
      'a.json: settings.starting_life must be an integer (found the number token 40.0)',
      'a.json: events[0].v has the number token 1.0; write an integral value as an integer',
      'a.json: events[0].seq has the number token 1e2; write an integral value as an integer',
      'a.json: expect.state.last_seq must be an integer (found the number token 1.0)',
    ]);
  });

  it('allows a fractional number only inside events', () => {
    const ok = reducerText('{}', '[{"type":"turn_passed","v":1.5}]', '{"state":{}}');
    expect(validateVectorText('a.json', ok)).toEqual([]);
    const bad =
      '{"format":1,"kind":"canonical_json","name":"a","description":"d","cases":[{"name":"x","input":1.5,"output":"1"}]}';
    expect(validateVectorText('a.json', bad)).toEqual([
      'a.json: cases[0].input must be an integer (found the number token 1.5)',
    ]);
  });

  it('rejects an integer outside the safe range outside events, not inside', () => {
    const big = '9007199254740993';
    const inEvents = reducerText('{}', `[{"type":"turn_passed","v":${big}}]`, '{"state":{}}');
    expect(validateVectorText('a.json', inEvents)).toEqual([]);
    const outside = `{"format":1,"kind":"canonical_json","name":"a","description":"d","cases":[{"name":"x","input":${big},"output":"1"}]}`;
    expect(validateVectorText('a.json', outside)).toEqual([
      `a.json: cases[0].input must be an integer in the safe range (found ${big})`,
    ]);
  });

  it('allows negative zero (canonical JSON writes it as 0)', () => {
    const text =
      '{"format":1,"kind":"canonical_json","name":"a","description":"d","cases":[{"name":"x","input":-0,"output":"0"}]}';
    expect(validateVectorText('a.json', text)).toEqual([]);
  });

  it('rejects a duplicate key', () => {
    const text = reducerText('{}', '[]', '{"state":{},"state":{}}');
    expect(validateVectorText('a.json', text)).toEqual(['a.json: expect.state is a duplicate key']);
  });

  it('ignores number-like text inside strings', () => {
    const text =
      '{"format":1,"kind":"canonical_json","name":"a","description":"1.0 \\" 2e5","cases":[{"name":"x","input":"1.0","output":"\\"1.0\\""}]}';
    expect(validateVectorText('a.json', text)).toEqual([]);
  });
});

describe('event-type coverage', () => {
  const allTypes = EVENT_TYPES.map((type, i) => event(type, { seq: i + 1 }));
  const file = (name: string, vector: Record<string, unknown>) => ({
    name: `${name}.json`,
    text: JSON.stringify(vector),
  });

  it('counts the events of a state vector and those before the rejected index', () => {
    const state = reducer('a', { events: [event('drain'), event('monarch_set')] });
    expect(exercisedEventTypes(state)).toEqual(['drain', 'monarch_set']);
    const rejected = reducer('a', {
      events: [event('drain'), event('monarch_set'), event('turn_passed')],
      expect: { error: { index: 1, code: 'invalid_phase' } },
    });
    expect(exercisedEventTypes(rejected)).toEqual(['drain']);
    expect(exercisedEventTypes(canonical('c', []))).toEqual([]);
  });

  it('passes when every registered type is exercised', () => {
    expect(checkCoverage([file('a', reducer('a', { events: allTypes }))])).toEqual([]);
  });

  it('fails with the exact message for a type no vector exercises', () => {
    const events = allTypes.filter((e) => e.type !== 'drain' && e.type !== 'game_resumed');
    expect(checkCoverage([file('a', reducer('a', { events }))])).toEqual([
      'uncovered event type: drain',
      'uncovered event type: game_resumed',
    ]);
  });

  it('does not count a type that appears only in a rejected event', () => {
    const rejected = reducer('r', {
      events: [event('turn_passed'), event('drain')],
      expect: { error: { index: 1, code: 'invalid_phase' } },
    });
    const files = [
      file('a', reducer('a', { events: allTypes.filter((e) => e.type !== 'drain') })),
      file('r', rejected),
    ];
    expect(checkCoverage(files)).toEqual(['uncovered event type: drain']);
  });

  it('skips an invalid vector file', () => {
    const broken = reducer('a', { events: allTypes, format: 2 });
    expect(checkCoverage([file('a', broken)])).toHaveLength(EVENT_TYPES.length);
  });

  it('fails the loader for a missing or empty directory and reads a temp directory', () => {
    const root = mkdtempSync(join(tmpdir(), 'vectors-'));
    try {
      expect(() => loadVectorFiles(join(root, 'nope'))).toThrow(/vectors directory not found/);
      expect(() => loadVectorFiles(root)).toThrow(/vectors directory has no \.json files/);
      const events = allTypes.slice(1);
      writeFileSync(join(root, 'a.json'), JSON.stringify(reducer('a', { events })));
      const files = loadVectorFiles(root);
      expect(files.map((f) => f.name)).toEqual(['a.json']);
      expect(checkCoverage(files)).toEqual([`uncovered event type: ${EVENT_TYPES[0]}`]);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });
});
