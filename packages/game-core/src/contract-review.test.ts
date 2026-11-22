import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { canonicalJson } from './canonical-json';
import { apply } from './reducer';
import { initialState } from './state';
import { SETTING_DEFAULTS, SUPPORTED_VERSIONS } from './types';
import type { EventEnvelope } from './types';

/**
 * The contract rules added in review (S-6.1.1), checked in the document so the document, the TS
 * package and the Python package cannot drift: versioning (1.1), the new state keys (3.1, 3.3),
 * `initialState` (3.5), "specified but not yet enforced" (4.3), reverts (6.5), the wire reader (5.6).
 */

const contract = readFileSync(new URL('../CONTRACT.md', import.meta.url), 'utf8');

function section(heading: string): string {
  const start = contract.indexOf(`\n${heading}\n`);
  if (start < 0) throw new Error(`CONTRACT.md has no heading ${heading}`);
  const rest = contract.slice(start + heading.length + 2);
  const end = rest.search(/\n#{1,3} /);
  return end < 0 ? rest : rest.slice(0, end);
}
function rows(heading: string): Map<string, string> {
  const map = new Map<string, string>();
  for (const m of section(heading).matchAll(/^\| `([a-z_]+)` *\|(.*)$/gm)) map.set(m[1]!, m[2]!);
  return map;
}

describe('state keys added in review', () => {
  it('defines `reverted` as ascending seqs, empty initially, pointing at 6.5', () => {
    const row = rows('### 3.1 Top level').get('reverted')!;
    expect(row).toContain('array of integers');
    expect(row).toContain('Ascending, no duplicates');
    expect(row).toContain('`[]` initially');
    expect(row).toContain('6.5');
  });

  it('defines the five flat elimination fields with their types', () => {
    const seat = rows('### 3.3 Seat object');
    expect([...seat.keys()].slice(-5)).toEqual([
      'elimination_reason',
      'eliminated_by',
      'eliminated_at_seq',
      'eliminated_round',
      'elimination_cause',
    ]);
    expect(seat.get('elimination_reason')).toContain('string or null');
    for (const key of ['eliminated_by', 'eliminated_at_seq', 'eliminated_round']) {
      expect(seat.get(key)).toContain('integer or null');
    }
    expect(seat.get('elimination_cause')).toContain('string or null');
    expect(seat.get('eliminated_at_seq')).toContain('D6');
  });

  it('states the invariant: eliminated if and only if eliminated_at_seq is not null', () => {
    const text = section('### 3.3 Seat object');
    expect(text).toContain('A seat is eliminated if and only if `eliminated_at_seq` is not `null`');
    expect(text).toContain('While the seat is in the game all five elimination fields are `null`');
    expect(text).toContain(
      '`elimination_reason`, `eliminated_at_seq` and `eliminated_round` are non-null',
    );
  });

  it('names the story-given fields', () => {
    const text = section('### 3.3 Seat object');
    expect(text).toContain('`eliminated_by` and `eliminated_at_seq` are the names');
    expect(text).toContain('S-6.1.3');
  });
});

describe('initialState rules (3.5, 6.1)', () => {
  const text = section('### 3.5 Initial state');
  it('says game_id must be a non-empty well-formed string and initialState throws', () => {
    expect(text).toContain('non-empty string without a lone surrogate');
    expect(text).toContain('**throws**');
    expect(text).toContain('never returns a reducer error code');
  });
  it('says settings are optional, partial, copied as given and unknown keys throw', () => {
    expect(text).toContain('optional and may be partial');
    expect(text).toContain('A missing key takes its default');
    expect(text).toContain('not one of the four setting names');
    expect(text).toContain('neither types nor ranges are validated');
  });
  it('says the settings are a placeholder replaced by game_created', () => {
    expect(text).toContain('**placeholder**');
    expect(text).toContain('`game_created`');
  });
  it('lists `reverted` in the initial state', () => {
    expect(text).toContain('`reverted` `[]`');
  });
  it('is the behaviour of the TS function', () => {
    expect(initialState('g').reverted).toEqual([]);
    expect(() => initialState('')).toThrow();
    expect(() => initialState('g', { nope: 1 } as never)).toThrow();
  });
});

describe('versioning (1.1)', () => {
  const text = section('### 1.1 Versioning');
  it('says v versions the envelope, the registered payloads and the state shape', () => {
    expect(text).toContain('**envelope**');
    expect(text).toContain('**registered payload shapes**');
    expect(text).toContain('**state shape**');
  });
  it('keeps every version still in a stored log supported', () => {
    expect(text).toContain('keeps accepting **every** version still present in a stored log');
    expect(text).toContain('`SUPPORTED_VERSIONS`');
    expect(SUPPORTED_VERSIONS).toContain(1);
    expect(text).toContain('`unsupported_version`');
  });
  it('lists which changes bump v and which do not', () => {
    const r = [...text.matchAll(/^\| (.+?) +\| (Yes|No) +\|/gm)].map((m) => [m[1]!.trim(), m[2]]);
    const bumps = new Map(r as [string, string][]);
    expect(bumps.get('Adding an event type')).toBe('No');
    expect(bumps.get('Adding a state key')).toBe('No');
    expect(bumps.get('Adding an error code')).toBe('No');
    expect(bumps.get('Adding an optional payload key')).toBe('No');
    expect(
      bumps.get('Changing the envelope keys or their rules, or the canonical JSON rules'),
    ).toBe('Yes');
    expect(
      bumps.get(
        'Removing, renaming or retyping a state key, or changing what an existing key means',
      ),
    ).toBe('Yes');
  });
});

describe('not yet enforced (4.3) really is not enforced', () => {
  const lobby = () => initialState('018f0000-0000-7000-8000-000000000001');
  const make = (type: string, payload: object, extra: object = {}) =>
    ({
      event_id: '00000000-0000-4000-8000-000000000001',
      game_id: lobby().game_id,
      type,
      payload,
      actor: { kind: 'system', player_id: null },
      on_behalf_of_seat: null,
      v: 1,
      ...extra,
    }) as unknown as EventEnvelope;

  it('states the rules', () => {
    const text = section('### 4.3 Specified but not yet enforced');
    expect(text).toContain('valid **only as the event at `seq` 1**');
    expect(text).toContain('`invalid_phase`');
    expect(text).toContain('**overwrites**');
    expect(text).toContain('Message size');
  });

  it('enforces game_created only as the event at seq 1 (S-6.1.6, 4.4.2)', () => {
    const settings = { ...SETTING_DEFAULTS, starting_life: 30 };
    const first = apply(lobby(), make('game_created', { settings }));
    expect(first.ok && first.state.settings).toEqual(settings);
    if (!first.ok) return;
    const again = apply(first.state, make('game_created', { settings }, { seq: 2 }));
    expect(again).toEqual({ ok: false, error: { code: 'invalid_phase' } });
  });

  it('enforces the settings ranges in game_created (S-6.1.6) but not in initialState', () => {
    expect(initialState('g', { starting_life: 0 }).settings.starting_life).toBe(0);
    const bad = apply(
      lobby(),
      make('game_created', { settings: { ...SETTING_DEFAULTS, starting_life: 0 } }),
    );
    expect(bad).toEqual({ ok: false, error: { code: 'invalid_setting' } });
  });

  it('accepts a first event that is not game_created', () => {
    expect(apply(lobby(), make('settings_changed', {})).ok).toBe(true);
  });

  it('leaves reverted and voided empty after an event_reverted (no-op handler)', () => {
    const r = apply(lobby(), make('event_reverted', { target_seq: 1 }));
    expect(r.ok && r.state.reverted).toEqual([]);
    expect(r.ok && r.state.voided).toEqual([]);
  });
});

describe('reverts and the wire reader (6.5, 5.6)', () => {
  it('says reverts are resolved by the fold over the log', () => {
    const text = section('### 6.5 Reverts are resolved by the fold over the log');
    expect(text).toContain('replay');
    expect(text).toContain('`not_revertible`');
    expect(text).toContain('`already_reverted`');
    expect(text).toContain('is **effective** unless a later effective `event_reverted` targets it');
    expect(text).toContain('`reverted` array');
    expect(text).toContain('`voided`');
  });

  it('says the wire reader rejects a lone surrogate escape as invalid_event', () => {
    const text = section('### 5.6 Reading JSON into the model (wire parsing)');
    expect(text).toContain('lone surrogate escape');
    expect(text).toContain('`invalid_event`');
    expect(text).toContain('`JSON.parse` accepts such an escape');
  });

  it('says a type with a lone surrogate is unknown_event_type and other envelope strings invalid_event', () => {
    const text = section('## 2. Event envelope');
    expect(text).toContain('**Well-formed strings.**');
    for (const field of ['`event_id`', '`game_id`', '`type`', '`ts`', '`actor.player_id`']) {
      expect(text).toContain(field);
    }
    expect(text).toContain('`unknown_event_type`, not `invalid_event`');
  });
});

describe('TS canonical serializer and class instances (5.4 "a class instance")', () => {
  it('rejects a class instance, a boxed string and a boxed number with unsupported_value', () => {
    class Foo {}
    for (const v of [new Foo(), Object('x'), Object(1), Object.create({ inherited: 1 })]) {
      expect(() => canonicalJson(v)).toThrow(/unsupported_value/);
    }
  });

  it('accepts a null-prototype object, and (pinned current behaviour) an Array subclass', () => {
    class Bag extends Array<number> {}
    // The contract does not say whether an Array subclass is "a class instance" (5.4). The TS
    // serializer treats it as an array; the Python one rejects a list subclass. Pinned here so a
    // change on either side is a conscious one; see the report to the coordinator.
    expect(canonicalJson(Bag.from([1, 2]))).toBe('[1,2]');
    expect(canonicalJson(Object.assign(Object.create(null) as object, { a: 1 }))).toBe('{"a":1}');
  });
});
