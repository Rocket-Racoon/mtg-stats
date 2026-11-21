import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { initialState } from './state';
import { PHASES, SETTING_DEFAULTS, SETTING_RANGES, TRUST_MODES, WIN_CONDITIONS } from './types';

/**
 * Acceptance criterion 1 (S-6.1.1): CONTRACT.md defines the event envelope, the state shape
 * (with the four game settings) and the reducer error codes. `contract.test.ts` keeps the
 * registry and the error-code tables in sync with the TS arrays; this file enforces the rest of
 * the criterion's list against the document and the TS state.
 */

const contract = readFileSync(new URL('../CONTRACT.md', import.meta.url), 'utf8');

function section(heading: string): string {
  const start = contract.indexOf(`\n${heading}\n`);
  if (start < 0) throw new Error(`CONTRACT.md has no heading ${heading}`);
  const rest = contract.slice(start + heading.length + 2);
  const end = rest.search(/\n#{1,3} /);
  return end < 0 ? rest : rest.slice(0, end);
}

/** Table rows keyed by the first-cell code span; the value is the whole row text. */
function rows(heading: string): Map<string, string> {
  const map = new Map<string, string>();
  for (const m of section(heading).matchAll(/^\| `([a-z_]+)` *\|(.*)$/gm)) map.set(m[1]!, m[2]!);
  return map;
}

const ENVELOPE_FIELDS = [
  'event_id',
  'game_id',
  'seq',
  'type',
  'payload',
  'actor',
  'on_behalf_of_seat',
  'ts',
  'v',
];

describe('criterion 1: the event envelope is defined', () => {
  const envelope = rows('## 2. Event envelope');

  it('defines exactly the nine envelope fields of the criterion, in order', () => {
    expect([...envelope.keys()]).toEqual(ENVELOPE_FIELDS);
  });

  it('says event_id is a client-generated UUID v4', () => {
    const row = envelope.get('event_id')!;
    expect(row).toContain('UUID v4');
    expect(row).toContain('client');
  });

  it('says seq is server-assigned and absent until acked', () => {
    const row = envelope.get('seq')!;
    expect(row).toContain('Server-assigned');
    expect(row).toContain('optional (absent)');
    expect(row).toContain('Absent until');
  });

  it('says game_id, type, payload and actor are required, and what ts and v are', () => {
    expect(envelope.get('game_id')).toContain('game');
    expect(envelope.get('type')).toContain('registered');
    expect(envelope.get('payload')).toContain('object');
    expect(envelope.get('actor')).toContain('"kind"');
    expect(envelope.get('ts')).toContain('Server UTC');
    expect(envelope.get('ts')).toContain('optional (absent)');
    expect(envelope.get('v')).toContain('Schema version');
  });

  it('says on_behalf_of_seat is nullable and always present', () => {
    const row = envelope.get('on_behalf_of_seat')!;
    expect(row).toContain('integer or null');
    expect(row).toContain('Always present');
  });

  it('lists the required keys', () => {
    const text = section('## 2. Event envelope');
    expect(text).toContain(
      'The required keys are `event_id`, `game_id`, `type`, `payload`, `actor`, `on_behalf_of_seat` and `v`.',
    );
  });

  it('gives the event_id pattern used by the reducers', () => {
    expect(envelope.get('event_id')).toContain(
      '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
    );
  });
});

describe('criterion 1: the state shape includes the game settings', () => {
  const top = rows('### 3.1 Top level');
  const settings = rows('### 3.2 `settings`');

  it('lists every state key, equal to the keys of initialState', () => {
    expect([...top.keys()].sort()).toEqual(Object.keys(initialState('g')).sort());
  });

  it('puts the settings object in the state', () => {
    expect(top.get('settings')).toContain('object');
  });

  it('defines the four settings, equal to the TS settings keys', () => {
    expect([...settings.keys()]).toEqual([
      'starting_life',
      'commander_damage_threshold',
      'poison_threshold',
      'trust_mode',
    ]);
    expect([...settings.keys()].sort()).toEqual(Object.keys(SETTING_DEFAULTS).sort());
    expect(Object.keys(initialState('g').settings).sort()).toEqual([...settings.keys()].sort());
  });

  it('gives each setting its range and default as the TS constants have them', () => {
    for (const [key, [lo, hi]] of Object.entries(SETTING_RANGES)) {
      const row = settings.get(key)!;
      expect(row).toContain(`${lo} to ${hi}`);
      expect(row).toContain(`| ${SETTING_DEFAULTS[key as keyof typeof SETTING_DEFAULTS]} `);
    }
  });

  it('defines trust_mode as casual or strict with casual as the default', () => {
    const row = settings.get('trust_mode')!;
    expect(row).toContain('`casual`, `strict`');
    expect(row).toContain('`casual`');
    expect([...TRUST_MODES]).toEqual(['casual', 'strict']);
    expect(SETTING_DEFAULTS.trust_mode).toBe('casual');
  });

  it('says out-of-range settings are invalid_setting and initialState does not validate', () => {
    const text = section('### 3.2 `settings`');
    expect(text).toContain('invalid_setting');
    expect(text).toContain('**`initialState` does not validate ranges**');
  });

  it('lists the phases the TS type has', () => {
    const row = top.get('phase')!;
    for (const phase of PHASES) expect(row).toContain(`\`${phase}\``);
  });

  it('lists every D5 win-condition code and no other', () => {
    const text = section('### 3.4 D5 win-condition codes');
    const listed = [...text.split('\n')[1]!.matchAll(/`([a-z_]+)`/g)].map((m) => m[1]);
    expect(listed.sort()).toEqual([...WIN_CONDITIONS].sort());
  });

  it('defines the seat object keys, equal to the keys of a seat in a realistic state', () => {
    const vector = JSON.parse(
      readFileSync(new URL('../vectors/canonical-json-game-state.json', import.meta.url), 'utf8'),
    ) as { cases: { input: { seats: Record<string, unknown>[] } }[] };
    const seatKeys = Object.keys(vector.cases[0]!.input.seats[0]!).sort();
    expect([...rows('### 3.3 Seat object').keys()].sort()).toEqual(seatKeys);
  });
});

describe('criterion 1: the contract lists the reducer error codes', () => {
  it('names the codes of criterion 4 in the reducer error table', () => {
    const codes = rows('### 6.2 Reducer error codes');
    expect(codes.get('unsupported_version')).toContain('`v`');
    expect(codes.get('unknown_event_type')).toContain('`type`');
    expect(codes.has('invalid_event')).toBe(true);
    expect(codes.has('out_of_order')).toBe(true);
    expect(codes.has('game_mismatch')).toBe(true);
    expect(codes.has('invalid_payload')).toBe(true);
  });

  it('states the check order that puts v before type', () => {
    const text = section('### 6.2 Reducer error codes');
    expect(text.indexOf('`unsupported_version`')).toBeLessThan(
      text.indexOf('`unknown_event_type`'),
    );
    expect(text).toContain(
      'so an event with both a bad `v` and a bad `type` gets `unsupported_version`',
    );
  });
});

describe('criterion 2 and 3 are stated in the contract', () => {
  it('states byte-identical canonical JSON for both reducers', () => {
    expect(contract).toContain('They must produce byte-identical canonical JSON');
  });

  it('forbids clocks, randomness and I/O in the reducer', () => {
    const text = section('## 7. Purity');
    for (const word of ['clock', 'randomness', 'network', 'file system', 'locale']) {
      expect(text).toContain(word);
    }
    expect(text).toContain('arrives **inside an event payload**');
  });
});
