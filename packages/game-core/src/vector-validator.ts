/**
 * Validator and coverage rule for the shared test vectors (CONTRACT.md section 8).
 *
 * Pure: it takes the text of a vector file and returns problems, so it holds no file access and is
 * not exported from `index.ts` (test tooling only). The file loader lives in
 * `vector-loader.test.helpers.ts`. The Python runner implements the same rules from section 8.
 */

import { EVENT_TYPES, REDUCER_ERROR_CODES, SETTING_DEFAULTS } from './types';

export const VECTOR_FORMAT = 1;
export const VECTOR_KINDS = ['reducer', 'canonical_json'] as const;
export const CANONICAL_ERROR_CODES = [
  'non_integer',
  'integer_out_of_range',
  'lone_surrogate',
  'unsupported_value',
] as const;
export const INVALID_KINDS = [
  'float',
  'nan',
  'infinity',
  'integer',
  'lone_surrogate',
  'deep_array',
  'deep_object',
] as const;
/** Largest chain length a `deep_array` / `deep_object` case may ask for. */
export const MAX_DEEP_CHAIN = 10000;

const MAX_SAFE = 9007199254740991n;

type Json = unknown;
type Obj = Record<string, unknown>;

function isObject(value: Json): value is Obj {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isNonEmptyString(value: Json): value is string {
  return typeof value === 'string' && value.length > 0;
}

function isNonNegativeInteger(value: Json): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
}

// ---------------------------------------------------------------------------
// Raw-text scan: number tokens and duplicate keys
// ---------------------------------------------------------------------------

/** A number token of the raw text, with the path of the value it belongs to. */
export interface NumberToken {
  path: string;
  token: string;
}

export interface TextScan {
  tokens: NumberToken[];
  duplicateKeys: string[];
}

/**
 * Walks valid JSON text and lists every number token with its path, plus every duplicate object
 * key. `JSON.parse` cannot tell `1.0` from `1`, nor see a repeated key, so these checks read the
 * text (CONTRACT.md 8.3). The text must already have been accepted by `JSON.parse`.
 */
export function scanText(text: string): TextScan {
  const tokens: NumberToken[] = [];
  const duplicateKeys: string[] = [];
  let i = 0;

  const skipSpace = (): void => {
    while (text[i] === ' ' || text[i] === '\t' || text[i] === '\n' || text[i] === '\r') i++;
  };

  const readString = (): string => {
    const start = i++;
    while (text[i] !== '"') i += text[i] === '\\' ? 2 : 1;
    i++;
    return JSON.parse(text.slice(start, i)) as string;
  };

  const join = (path: string, key: string): string => (path === '' ? key : `${path}.${key}`);

  const readValue = (path: string): void => {
    skipSpace();
    const ch = text[i];
    if (ch === '{') {
      i++;
      const seen = new Set<string>();
      skipSpace();
      if (text[i] === '}') {
        i++;
        return;
      }
      for (;;) {
        skipSpace();
        const key = readString();
        const keyPath = join(path, key);
        if (seen.has(key)) duplicateKeys.push(keyPath);
        seen.add(key);
        skipSpace();
        i++; // ':'
        readValue(keyPath);
        skipSpace();
        if (text[i++] === '}') return;
      }
    } else if (ch === '[') {
      i++;
      skipSpace();
      if (text[i] === ']') {
        i++;
        return;
      }
      for (let n = 0; ; n++) {
        readValue(`${path}[${n}]`);
        skipSpace();
        if (text[i++] === ']') return;
      }
    } else if (ch === '"') {
      readString();
    } else if (ch === '-' || (ch !== undefined && ch >= '0' && ch <= '9')) {
      const start = i;
      while (i < text.length && /[-+.eE0-9]/.test(text[i]!)) i++;
      tokens.push({ path, token: text.slice(start, i) });
    } else {
      while (i < text.length && /[a-z]/.test(text[i]!)) i++; // true, false, null
    }
  };

  readValue('');
  return { tokens, duplicateKeys };
}

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

const COMMON_KEYS = ['format', 'kind', 'name', 'description'];
const REDUCER_KEYS = [...COMMON_KEYS, 'game_id', 'settings', 'events', 'expect'];
const CANONICAL_KEYS = [...COMMON_KEYS, 'cases'];

/**
 * Validates the text of one vector file and returns every problem, each formatted
 * `<file>: <path> <message>` (CONTRACT.md 8.3). An empty list means the file is valid.
 * `fileName` is the base name with `.json`; `name` must equal it without the extension.
 */
export function validateVectorText(fileName: string, text: string): string[] {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch (e) {
    return [`${fileName}: invalid JSON: ${e instanceof Error ? e.message : String(e)}`];
  }
  const problems: string[] = [];
  const report = (path: string, message: string): void => {
    problems.push(`${fileName}: ${path} ${message}`);
  };

  if (!isObject(value)) return [`${fileName}: $ must be an object`];

  const kind = value.kind;
  const validKind = kind === 'reducer' || kind === 'canonical_json';
  if (!('kind' in value)) report('kind', 'is required');
  else if (!validKind) report('kind', 'must be "reducer" or "canonical_json"');

  if (!('format' in value)) report('format', 'is required');
  else if (value.format !== VECTOR_FORMAT) report('format', 'must be the integer 1');

  const stem = fileName.replace(/\.json$/, '');
  if (!('name' in value)) report('name', 'is required');
  else if (value.name !== stem) report('name', `must equal "${stem}"`);

  if (!('description' in value)) report('description', 'is required');
  else if (!isNonEmptyString(value.description))
    report('description', 'must be a non-empty string');

  if (kind === 'reducer') validateReducer(value, report);
  else if (kind === 'canonical_json') validateCanonical(value, report);

  if (validKind) {
    const allowed = kind === 'reducer' ? REDUCER_KEYS : CANONICAL_KEYS;
    for (const key of Object.keys(value)) {
      if (!allowed.includes(key)) report(key, 'is not a known key');
    }
  }

  const scan = scanText(text);
  const eventsZone = kind === 'reducer';
  for (const { path, token } of scan.tokens) {
    const inEvents = eventsZone && path.startsWith('events[');
    const isIntegerSyntax = /^-?\d+$/.test(token);
    if (!isIntegerSyntax) {
      const number = Number(token);
      if (inEvents) {
        if (!Number.isFinite(number) || Number.isInteger(number)) {
          report(path, `has the number token ${token}; write an integral value as an integer`);
        }
      } else {
        report(path, `must be an integer (found the number token ${token})`);
      }
    } else if (!inEvents) {
      const magnitude = BigInt(token) < 0n ? -BigInt(token) : BigInt(token);
      if (magnitude > MAX_SAFE)
        report(path, `must be an integer in the safe range (found ${token})`);
    }
  }
  for (const path of scan.duplicateKeys) report(path, 'is a duplicate key');

  return problems;
}

type Report = (path: string, message: string) => void;

function requireKey(obj: Obj, key: string, path: string, report: Report): boolean {
  if (key in obj) return true;
  report(path, 'is required');
  return false;
}

function validateReducer(vector: Obj, report: Report): void {
  if (requireKey(vector, 'game_id', 'game_id', report) && !isNonEmptyString(vector.game_id)) {
    report('game_id', 'must be a non-empty string');
  }

  if (requireKey(vector, 'settings', 'settings', report)) {
    const settings = vector.settings;
    if (!isObject(settings)) report('settings', 'must be an object');
    else {
      for (const key of Object.keys(settings)) {
        if (!(key in SETTING_DEFAULTS)) report(`settings.${key}`, 'is not a known key');
      }
    }
  }

  const events = vector.events;
  let eventCount: number | null = null;
  if (requireKey(vector, 'events', 'events', report)) {
    if (!Array.isArray(events)) report('events', 'must be an array');
    else {
      eventCount = events.length;
      events.forEach((event: unknown, i) => {
        if (!isObject(event)) report(`events[${i}]`, 'must be an object');
      });
    }
  }

  if (!requireKey(vector, 'expect', 'expect', report)) return;
  const expectValue = vector.expect;
  if (!isObject(expectValue)) {
    report('expect', 'must be an object');
    return;
  }
  for (const key of Object.keys(expectValue)) {
    if (key !== 'state' && key !== 'error') report(`expect.${key}`, 'is not a known key');
  }
  const hasState = 'state' in expectValue;
  const hasError = 'error' in expectValue;
  if (hasState === hasError) report('expect', 'must have exactly one of "state" or "error"');
  if (hasState && !isObject(expectValue.state)) report('expect.state', 'must be an object');

  // Events before `limit` are applied, so each needs a string `type`; the rejected event and the
  // ones after it may be malformed on purpose (the `reject-type-*` vectors).
  let limit = eventCount ?? 0;
  if (hasError) {
    const error = expectValue.error;
    if (!isObject(error)) report('expect.error', 'must be an object');
    else {
      for (const key of Object.keys(error)) {
        if (key !== 'index' && key !== 'code') report(`expect.error.${key}`, 'is not a known key');
      }
      if (requireKey(error, 'index', 'expect.error.index', report)) {
        if (!isNonNegativeInteger(error.index)) {
          report('expect.error.index', 'must be a non-negative integer');
        } else if (eventCount !== null && error.index >= eventCount) {
          report('expect.error.index', `must be less than the number of events (${eventCount})`);
        } else if (eventCount !== null) limit = error.index;
      }
      if (requireKey(error, 'code', 'expect.error.code', report)) {
        if (
          typeof error.code !== 'string' ||
          !(REDUCER_ERROR_CODES as readonly string[]).includes(error.code)
        ) {
          report('expect.error.code', 'must be a known reducer error code');
        }
      }
    }
  }
  if (Array.isArray(events)) {
    events.slice(0, limit).forEach((event: unknown, i) => {
      if (isObject(event) && typeof event.type !== 'string') {
        report(`events[${i}].type`, 'must be a string');
      }
    });
  }
}

function validateCanonical(vector: Obj, report: Report): void {
  if (!requireKey(vector, 'cases', 'cases', report)) return;
  const cases = vector.cases;
  if (!Array.isArray(cases) || cases.length === 0) {
    report('cases', 'must be a non-empty array');
    return;
  }
  const names = new Map<string, number>();
  cases.forEach((c: unknown, i) => {
    const at = `cases[${i}]`;
    if (!isObject(c)) {
      report(at, 'must be an object');
      return;
    }
    if (!requireKey(c, 'name', `${at}.name`, report)) {
      /* reported */
    } else if (!isNonEmptyString(c.name)) report(`${at}.name`, 'must be a non-empty string');
    else if (names.has(c.name)) {
      report(`${at}.name`, `duplicates the name of cases[${names.get(c.name)}]`);
    } else names.set(c.name, i);

    const hasInput = 'input' in c;
    const hasInvalid = 'invalid' in c;
    if (hasInput === hasInvalid) {
      report(at, 'must have exactly one of "input" or "invalid"');
      return;
    }
    const allowed = hasInput ? ['name', 'input', 'output'] : ['name', 'invalid', 'error'];
    for (const key of Object.keys(c)) {
      if (!allowed.includes(key)) report(`${at}.${key}`, 'is not a known key');
    }
    if (hasInput) {
      if (requireKey(c, 'output', `${at}.output`, report) && typeof c.output !== 'string') {
        report(`${at}.output`, 'must be a string');
      }
    } else {
      validateInvalidCase(c, at, report);
    }
  });
}

function validateInvalidCase(c: Obj, at: string, report: Report): void {
  if (requireKey(c, 'error', `${at}.error`, report)) {
    if (
      typeof c.error !== 'string' ||
      !(CANONICAL_ERROR_CODES as readonly string[]).includes(c.error)
    ) {
      report(`${at}.error`, 'must be a canonical JSON error code');
    }
  }
  const invalid = c.invalid;
  if (!isObject(invalid)) {
    report(`${at}.invalid`, 'must be an object');
    return;
  }
  for (const key of Object.keys(invalid)) {
    if (key !== 'kind' && key !== 'value') report(`${at}.invalid.${key}`, 'is not a known key');
  }
  const kind = invalid.kind;
  if (!('kind' in invalid)) {
    report(`${at}.invalid.kind`, 'is required');
    return;
  }
  if (typeof kind !== 'string' || !(INVALID_KINDS as readonly string[]).includes(kind)) {
    report(`${at}.invalid.kind`, `must be one of ${INVALID_KINDS.join(', ')}`);
    return;
  }
  const path = `${at}.invalid.value`;
  if (kind === 'nan' || kind === 'infinity') {
    if ('value' in invalid) report(path, 'must be absent');
    return;
  }
  if (!requireKey(invalid, 'value', path, report)) return;
  const v = invalid.value;
  const text = typeof v === 'string' ? v : '';
  if (kind === 'float') {
    const number = Number(text);
    if (
      !/^-?(0|[1-9]\d*)(\.\d+)?([eE][-+]?\d+)?$/.test(text) ||
      !Number.isFinite(number) ||
      Number.isInteger(number)
    ) {
      report(path, 'must be decimal text of a non-integer number');
    }
  } else if (kind === 'integer') {
    const ok = /^-?(0|[1-9]\d*)$/.test(text);
    const magnitude = ok ? (BigInt(text) < 0n ? -BigInt(text) : BigInt(text)) : 0n;
    if (!ok || magnitude <= MAX_SAFE) {
      report(path, 'must be decimal text of an integer outside the safe range');
    }
  } else if (kind === 'lone_surrogate') {
    if (!/^[dD][89a-fA-F][0-9a-fA-F]{2}$/.test(text)) {
      report(path, 'must be the 4-digit hex code of a surrogate code unit (d800 to dfff)');
    }
  } else if (!/^[1-9]\d*$/.test(text) || Number(text) > MAX_DEEP_CHAIN) {
    report(path, `must be decimal text of an integer from 1 to ${MAX_DEEP_CHAIN}`);
  }
}

// ---------------------------------------------------------------------------
// Event-type coverage (CONTRACT.md 8.4)
// ---------------------------------------------------------------------------

/**
 * The event types a valid reducer vector exercises: the `type` of every event that is applied
 * successfully, that is every event of a vector that expects a state and the events before
 * `expect.error.index` of a vector that expects an error. The rejected event, and any event after
 * it, does not count.
 */
export function exercisedEventTypes(vector: Obj): string[] {
  if (vector.kind !== 'reducer' || !Array.isArray(vector.events) || !isObject(vector.expect)) {
    return [];
  }
  const expectValue = vector.expect;
  const applied =
    isObject(expectValue.error) && typeof expectValue.error.index === 'number'
      ? vector.events.slice(0, expectValue.error.index)
      : vector.events;
  const types: string[] = [];
  for (const event of applied as unknown[]) {
    if (isObject(event) && typeof event.type === 'string') types.push(event.type);
  }
  return types;
}

/** The message for a registered type that no vector exercises. */
export function uncoveredMessage(type: string): string {
  return `uncovered event type: ${type}`;
}

/**
 * Checks the coverage rule over the texts of the vector files. A file that fails validation is
 * skipped here (it fails its own test). Returns one message per uncovered type, in registry order.
 */
export function checkCoverage(
  files: readonly { name: string; text: string }[],
  registry: readonly string[] = EVENT_TYPES,
): string[] {
  const seen = new Set<string>();
  for (const { name, text } of files) {
    if (validateVectorText(name, text).length > 0) continue;
    for (const type of exercisedEventTypes(JSON.parse(text) as Obj)) seen.add(type);
  }
  return registry.filter((type) => !seen.has(type)).map(uncoveredMessage);
}
