import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { canonicalJson, CanonicalJsonError } from './canonical-json';
import { replay } from './reducer';
import { initialState } from './state';
import type { EventEnvelope, GameSettings } from './types';
import { validateVectorText } from './vector-validator';

/**
 * Node-only vector tooling for the tests (CONTRACT.md section 8). Kept out of `index.ts` and
 * named `*.test.helpers.ts` so the purity guard treats it as test code and no bundle imports it.
 */

export interface VectorFile {
  /** Base name with `.json`. */
  name: string;
  text: string;
  /** Set when the bytes are not valid UTF-8; `text` is then empty. */
  decodeError?: string;
}

/**
 * Reads every `*.json` file of a directory, sorted by name. Fails loudly when the directory is
 * missing or holds no vector file.
 */
export function loadVectorFiles(dir: string): VectorFile[] {
  if (!existsSync(dir) || !statSync(dir).isDirectory()) {
    throw new Error(`vectors directory not found: ${dir}`);
  }
  // Only regular files directly in the directory whose name ends in exactly `.json`: a directory,
  // a symbolic link (never followed, even a dangling one) and `x.JSON` are ignored (CONTRACT.md 8.5).
  const names = readdirSync(dir, { withFileTypes: true })
    .filter((entry) => entry.isFile() && entry.name.endsWith('.json'))
    .map((entry) => entry.name)
    .sort();
  if (names.length === 0) throw new Error(`vectors directory has no .json files: ${dir}`);
  // Strict UTF-8; the BOM is kept so that the validator reports it as invalid JSON.
  const decoder = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true });
  return names.map((name) => {
    try {
      return { name, text: decoder.decode(readFileSync(join(dir, name))) };
    } catch (e) {
      return { name, text: '', decodeError: e instanceof Error ? e.message : String(e) };
    }
  });
}

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

/**
 * Validates and runs one vector file. Throws an Error whose message starts with the file name
 * (and the case name for a canonical case) when the file is invalid or the expectation fails.
 */
export function runVectorFile(file: VectorFile): void {
  try {
    runChecked(file);
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    if (message.startsWith(`${file.name}: `)) throw e;
    throw new Error(`${file.name}: internal error: ${message}`, { cause: e });
  }
}

interface CanonicalCase {
  name: string;
  input?: unknown;
  output?: string;
  invalid?: { kind: string; value?: string };
  error?: string;
}

/** Runs one canonical case; returns a failure detail, or null when the case holds. */
function canonicalCaseFailure(c: CanonicalCase): string | null {
  if (c.invalid) {
    // Every allowed `integer` value (at most 300 digits) is a finite double above 2^53 - 1, which
    // the serializer rejects as `integer_out_of_range`; `Number()` is lossless enough for that.
    const value = invalidValue(c.invalid);
    // Deep chains are serialized as they are, every other kind inside a one-element array.
    const input = c.invalid.kind.startsWith('deep_') ? value : [value];
    try {
      canonicalJson(input);
    } catch (e) {
      if (e instanceof CanonicalJsonError && e.code === c.error) return null;
      return `expected error ${c.error}, got ${String(e)}`;
    }
    return `expected error ${c.error}, but serializing succeeded`;
  }
  const actual = canonicalJson(c.input);
  return actual === c.output ? null : `expected ${c.output}, got ${actual}`;
}

function runChecked(file: VectorFile): void {
  if (file.decodeError !== undefined) {
    throw new Error(`${file.name}: invalid JSON: not valid UTF-8: ${file.decodeError}`);
  }
  const problems = validateVectorText(file.name, file.text);
  if (problems.length > 0) throw new Error(problems.join('\n'));
  const vector = JSON.parse(file.text) as Record<string, unknown>;

  if (vector.kind === 'canonical_json') {
    // Every failing case of the file is reported, in one failure (CONTRACT.md 8.5).
    const failures: string[] = [];
    for (const c of vector.cases as CanonicalCase[]) {
      let detail: string | null;
      try {
        detail = canonicalCaseFailure(c);
      } catch (e) {
        detail = `unexpected error: ${String(e)}`;
      }
      if (detail !== null) failures.push(`${file.name}: case "${c.name}": ${detail}`);
    }
    if (failures.length > 0) throw new Error(failures.join('\n'));
    return;
  }

  const gameId = vector.game_id as string;
  const events = (vector.events as Record<string, unknown>[]).map((e) => ({
    game_id: gameId,
    ...e,
  })) as unknown as EventEnvelope[];
  const result = replay(initialState(gameId, vector.settings as Partial<GameSettings>), events);
  const expected = vector.expect as {
    state?: unknown;
    error?: { index: number; code: string };
  };
  if (expected.error) {
    if (result.ok) {
      throw new Error(
        `${file.name}: expected event ${expected.error.index} to be rejected with ${expected.error.code}, but every event was accepted`,
      );
    }
    const actual = { index: result.index, code: result.error.code };
    if (actual.index !== expected.error.index || actual.code !== expected.error.code) {
      throw new Error(
        `${file.name}: expected ${JSON.stringify(expected.error)}, got ${JSON.stringify(actual)}`,
      );
    }
    return;
  }
  if (!result.ok) {
    throw new Error(
      `${file.name}: event ${result.index} was rejected with ${result.error.code}, expected the final state`,
    );
  }
  const actualText = canonicalJson(result.state);
  const expectedText = canonicalJson(expected.state);
  if (actualText !== expectedText) {
    throw new Error(
      `${file.name}: final state differs\n  expected ${expectedText}\n  actual   ${actualText}`,
    );
  }
}
