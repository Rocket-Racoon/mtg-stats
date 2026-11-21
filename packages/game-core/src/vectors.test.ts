import { readdirSync, readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { canonicalJson, CanonicalJsonError } from './canonical-json';
import { replay } from './reducer';
import { initialState } from './state';
import type { EventEnvelope, GameSettings } from './types';

/**
 * Minimal runner for the seed vectors (format in CONTRACT.md section 8). S-6.1.2 adds the
 * validator, the coverage check and the Python runner; it will replace this file.
 */

interface CanonicalCase {
  name: string;
  input?: unknown;
  output?: string;
  invalid?: { kind: string; value?: string };
  error?: string;
}
interface CanonicalVector {
  kind: 'canonical_json';
  cases: CanonicalCase[];
}
interface ReducerVector {
  kind: 'reducer';
  game_id: string;
  settings: GameSettings;
  events: Record<string, unknown>[];
  expect: { state: unknown } | { error: { index: number; code: string } };
}

const dir = new URL('../vectors/', import.meta.url);
const files = readdirSync(dir).filter((f) => f.endsWith('.json'));

function chain(count: number, kind: 'array' | 'object'): unknown {
  let value: unknown = kind === 'array' ? [] : {};
  for (let i = 1; i < count; i++) value = kind === 'array' ? [value] : { a: value };
  return value;
}

function invalidValue(spec: { kind: string; value?: string }): unknown {
  switch (spec.kind) {
    case 'float':
    case 'integer':
      return Number(spec.value);
    case 'nan':
      return Number.NaN;
    case 'infinity':
      return Number.POSITIVE_INFINITY;
    case 'deep_array':
    case 'deep_object':
      return chain(Number(spec.value), spec.kind === 'deep_array' ? 'array' : 'object');
    case 'lone_surrogate':
      return String.fromCharCode(parseInt(spec.value ?? '', 16));
    default:
      throw new Error(`unknown invalid kind ${spec.kind}`);
  }
}

describe('seed vectors', () => {
  it('finds the vector files', () => {
    expect(files).toEqual(expect.arrayContaining(['canonical-json.json', 'initial-state.json']));
  });

  describe.each(files)('%s', (file) => {
    const vector = JSON.parse(readFileSync(new URL(file, dir), 'utf8')) as
      CanonicalVector | ReducerVector;

    if (vector.kind === 'canonical_json') {
      it.each(vector.cases)('$name', (c) => {
        if (c.invalid) {
          try {
            const value = invalidValue(c.invalid);
            // Deep chains are serialized as they are, every other kind inside a one-element array.
            canonicalJson(c.invalid.kind.startsWith('deep_') ? value : [value]);
            expect.unreachable('expected a CanonicalJsonError');
          } catch (e) {
            expect(e).toBeInstanceOf(CanonicalJsonError);
            expect((e as CanonicalJsonError).code).toBe(c.error);
          }
        } else {
          expect(canonicalJson(c.input)).toBe(c.output);
        }
      });
    } else {
      it('matches its expectation', () => {
        const events = vector.events.map((e) => ({
          game_id: vector.game_id,
          ...e,
        })) as unknown as EventEnvelope[];
        const result = replay(initialState(vector.game_id, vector.settings), events);
        if ('state' in vector.expect) {
          expect(result.ok).toBe(true);
          if (result.ok) {
            expect(canonicalJson(result.state)).toBe(canonicalJson(vector.expect.state));
          }
        } else {
          expect(result.ok).toBe(false);
          if (!result.ok) {
            expect({ index: result.index, code: result.error.code }).toEqual(vector.expect.error);
          }
        }
      });
    }
  });
});
