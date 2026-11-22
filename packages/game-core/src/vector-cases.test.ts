import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { checkCoverage, validateVectorText } from './vector-validator';

/**
 * The shared corpus of validator cases (S-6.1.2, CONTRACT.md 8.3, 8.3.1 and 8.4). The same files in
 * `packages/game-core/vector-cases/` are executed by the Python suite
 * (`apps/api/app/games/tests/test_vector_cases.py`), so the two validators cannot drift apart
 * unnoticed. The format is described in `vector-cases/README.md`. Expected messages come from the
 * contract's tables, never from a validator.
 */

type Message = string | { startsWith: string };

interface ValidatorCase {
  kind: 'validator';
  name: string;
  file: string;
  text: string;
  messages?: Message[];
  /** No longer allowed: a case states one list of messages (README, CONTRACT.md 8). */
  gap?: unknown;
  alternatives?: unknown;
}

interface CoverageCase {
  kind: 'coverage';
  name: string;
  registry: string[];
  files: { file: string; text: string }[];
  messages: string[];
}

type Case = ValidatorCase | CoverageCase;

const dir = fileURLToPath(new URL('../vector-cases/', import.meta.url));

function loadCases(): Case[] {
  if (!existsSync(dir)) throw new Error(`vector-cases directory not found: ${dir}`);
  const names = readdirSync(dir)
    .filter((f) => f.endsWith('.json'))
    .sort();
  if (names.length === 0) throw new Error(`vector-cases directory has no .json files: ${dir}`);
  return names.map((n) => {
    const doc = JSON.parse(readFileSync(join(dir, n), 'utf8')) as Case & { format: number };
    if (doc.format !== 1) throw new Error(`${n}: format must be 1`);
    if (doc.name !== n.replace(/\.json$/, ''))
      throw new Error(`${n}: name must equal the file stem`);
    return doc;
  });
}

const cases = loadCases();
const validatorCases = cases.filter((c): c is ValidatorCase => c.kind === 'validator');
const coverageCases = cases.filter((c): c is CoverageCase => c.kind === 'coverage');

/** Replaces each actual message that satisfies a `startsWith` entry by that entry, then compares. */
function agrees(actual: string[], expected: Message[]): boolean {
  if (actual.length !== expected.length) return false;
  return expected.every((e, i) =>
    typeof e === 'string' ? actual[i] === e : actual[i]!.startsWith(e.startsWith),
  );
}

describe('validator case corpus', () => {
  it('holds both kinds of case, with every case named after its file', () => {
    expect(validatorCases.length).toBeGreaterThanOrEqual(300);
    expect(coverageCases.length).toBeGreaterThanOrEqual(20);
    expect(new Set(cases.map((c) => c.name)).size).toBe(cases.length);
    for (const c of validatorCases) {
      expect(c.file.endsWith('.json'), c.name).toBe(true);
      // A case with "alternatives" could pass on one reading in one language and on another in the
      // other, hiding drift: every case states exactly one list of messages.
      expect(c.alternatives, `${c.name}: alternatives are not allowed`).toBeUndefined();
      expect(c.gap, `${c.name}: gap is not allowed`).toBeUndefined();
      expect(Array.isArray(c.messages), `${c.name}: messages`).toBe(true);
    }
  });

  // Each message template of CONTRACT.md 8.3 / 8.3.1 appears in at least one case's expectation,
  // so no rule of the contract goes unpinned.
  const templates = [
    'invalid JSON: ',
    '$ must be an object',
    'kind is required',
    'kind must be "reducer" or "canonical_json"',
    'format is required',
    'format must be the integer 1',
    'name is required',
    'name must equal "',
    'description is required',
    'description must be a non-empty string',
    'game_id is required',
    'game_id must be a non-empty string',
    'settings is required',
    'settings must be an object',
    'settings.zeta is not a known key',
    'events is required',
    'events must be an array',
    'events[0] must be an object',
    'expect is required',
    'expect must be an object',
    'expect.extra is not a known key',
    'expect must have exactly one of "state" or "error"',
    'expect.state must be an object',
    'expect.error must be an object',
    'expect.error.z is not a known key',
    'expect.error.index is required',
    'expect.error.index must be a non-negative integer',
    'expect.error.index must be less than the number of events (',
    'expect.error.code is required',
    'expect.error.code must be a known reducer error code',
    'events[1].type must be a string',
    ' is not a known key',
    'has the number token ',
    'write an integral value as an integer',
    'must be an integer (found the number token ',
    'must be an integer in the safe range (found ',
    ' is a duplicate key',
    'cases is required',
    'cases must be a non-empty array',
    'cases[0] must be an object',
    'cases[0].name is required',
    'cases[0].name must be a non-empty string',
    'cases[2].name duplicates the name of cases[0]',
    'must have exactly one of "input" or "invalid"',
    'cases[0].output is required',
    'cases[0].output must be a string',
    'cases[0].error is required',
    'cases[0].error must be a canonical JSON error code',
    'cases[0].invalid must be an object',
    'cases[0].invalid.z is not a known key',
    'cases[0].invalid.kind is required',
    'cases[0].invalid.kind must be one of float, nan, infinity, integer, lone_surrogate, deep_array, deep_object',
    'cases[0].invalid.value must be absent',
    'cases[0].invalid.value is required',
    'must be decimal text of a non-integer number',
    'must be decimal text of an integer outside the safe range',
    'must be the 4-digit hex code of a surrogate code unit (d800 to dfff)',
    'must be decimal text of an integer from 1 to 10000',
  ];
  const everyMessage = validatorCases.flatMap((c) =>
    (c.messages ?? []).map((m) => (typeof m === 'string' ? m : m.startsWith)),
  );
  it.each(templates)('some case pins the message %j', (template) => {
    expect(everyMessage.some((m) => m.includes(template))).toBe(true);
  });
});

describe('validators agree with the shared cases', () => {
  it.each(validatorCases.map((c) => c.name))('%s', (name) => {
    const c = validatorCases.find((x) => x.name === name)!;
    const actual = validateVectorText(c.file, c.text);
    const messages = c.messages!;
    expect(agrees(actual, messages), `got ${JSON.stringify(actual)}`).toBe(true);
    expect(actual.length).toBe(messages.length);
    if (messages.every((m) => typeof m === 'string')) expect(actual).toEqual(messages);
  });
});

describe('coverage rule agrees with the shared cases', () => {
  it.each(coverageCases.map((c) => c.name))('%s', (name) => {
    const c = coverageCases.find((x) => x.name === name)!;
    const files = c.files.map((f) => ({ name: f.file, text: f.text }));
    expect(checkCoverage(files, c.registry)).toEqual(c.messages);
  });
});
