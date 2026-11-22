/**
 * Validator and coverage rule for the shared test vectors (CONTRACT.md section 8).
 *
 * Pure: it takes the text of a vector file and returns problems, so it holds no file access and is
 * not exported from `index.ts` (test tooling only). The file loader lives in
 * `vector-loader.test.helpers.ts`. The Python runner implements the same rules from section 8.
 *
 * `validateVectorText` never throws: any internal failure becomes one message for the file.
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
/** Most digits (after an optional `-`) of `invalid.value` for the kind `integer`. */
export const MAX_INTEGER_DIGITS = 300;
/** Deepest container nesting a vector file's text may have (8.3). */
export const MAX_TEXT_DEPTH = 512;

const MAX_SAFE = 9007199254740991n;
const SETTING_NAMES: ReadonlySet<string> = new Set(Object.keys(SETTING_DEFAULTS));

type Json = unknown;
type Obj = Record<string, unknown>;

function has(obj: Obj, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(obj, key);
}

function get(obj: Obj, key: string): unknown {
  return has(obj, key) ? obj[key] : undefined;
}

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
// Raw-text checks: nesting depth, number tokens, duplicate keys, key order
// ---------------------------------------------------------------------------

/**
 * The deepest container nesting of a text, counted without parsing it: `[` and `{` outside strings
 * open a level and `]` and `}` close one (never below 0); inside a string a backslash skips the
 * next character and an unescaped `"` ends it. The top-level container is level 1.
 */
export function textDepth(text: string): number {
  let depth = 0;
  let max = 0;
  let inString = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inString) {
      if (ch === '\\') i++;
      else if (ch === '"') inString = false;
    } else if (ch === '"') inString = true;
    else if (ch === '[' || ch === '{') {
      depth++;
      if (depth > max) max = depth;
    } else if ((ch === ']' || ch === '}') && depth > 0) depth--;
  }
  return max;
}

/** A number token of the raw text, with the path of the value it belongs to. */
export interface NumberToken {
  path: string;
  token: string;
}

export interface TextScan {
  tokens: NumberToken[];
  /** One entry per repeated occurrence of a key, in document order. */
  duplicateKeys: string[];
  /**
   * The distinct keys of every object in document order (first occurrence), by key path. A key path
   * is `''` for the root and then `/<JSON string of the key>` or `/<index>` per step, which cannot
   * collide whatever the keys contain. For a repeated object path the last occurrence wins.
   */
  objectKeys: Map<string, string[]>;
}

const keyStep = (kp: string, step: string | number): string =>
  `${kp}/${typeof step === 'number' ? step : JSON.stringify(step)}`;

interface Frame {
  object: boolean;
  path: string;
  kp: string;
  keys: string[];
  seen: Set<string>;
  count: number;
}

/**
 * Walks valid JSON text with an explicit stack (so depth is limited only by memory) and lists every
 * number token with its path, every duplicate key and the key order of every object. `JSON.parse`
 * cannot tell `1.0` from `1`, nor see a repeated key, nor give integer-like keys in document order,
 * so these checks read the text (CONTRACT.md 8.3, 8.3.1). The text must already have been accepted
 * by `JSON.parse`.
 */
export function scanText(text: string): TextScan {
  const tokens: NumberToken[] = [];
  const duplicateKeys: string[] = [];
  const objectKeys = new Map<string, string[]>();
  const stack: Frame[] = [];
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

  /** Reads one value: a scalar completely, a container by pushing its frame. */
  const readValue = (path: string, kp: string): void => {
    skipSpace();
    const ch = text[i];
    if (ch === '{' || ch === '[') {
      i++;
      stack.push({ object: ch === '{', path, kp, keys: [], seen: new Set(), count: 0 });
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

  readValue('', '');
  while (stack.length > 0) {
    const frame = stack[stack.length - 1]!;
    skipSpace();
    const close = frame.object ? '}' : ']';
    if (text[i] === close) {
      i++;
      stack.pop();
      if (frame.object) objectKeys.set(frame.kp, frame.keys);
      continue;
    }
    if (text[i] === ',') {
      i++;
      skipSpace();
    }
    if (frame.object) {
      const key = readString();
      const keyPath = join(frame.path, key);
      if (frame.seen.has(key)) duplicateKeys.push(keyPath);
      else {
        frame.seen.add(key);
        frame.keys.push(key);
      }
      skipSpace();
      i++; // ':'
      readValue(keyPath, keyStep(frame.kp, key));
    } else {
      readValue(`${frame.path}[${frame.count}]`, keyStep(frame.kp, frame.count));
      frame.count++;
    }
  }
  return { tokens, duplicateKeys, objectKeys };
}

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

const COMMON_KEYS = ['format', 'kind', 'name', 'description'];
const REDUCER_KEYS = [...COMMON_KEYS, 'game_id', 'settings', 'events', 'expect'];
const CANONICAL_KEYS = [...COMMON_KEYS, 'cases'];

type Report = (path: string, message: string) => void;

/** What the kind-specific checks need: the reporter and the document order of an object's keys. */
interface Ctx {
  report: Report;
  /** The keys of `obj` (found at key path `kp`) in document order. */
  keys: (obj: Obj, kp: string) => string[];
}

/**
 * Validates the text of one vector file and returns every problem, each formatted
 * `<file>: <path> <message>` (CONTRACT.md 8.3). An empty list means the file is valid.
 * `fileName` is the base name with `.json`; `name` must equal it without the extension.
 * Never throws: an unexpected internal error is returned as `<file>: $ internal error: <detail>`.
 */
export function validateVectorText(fileName: string, text: string): string[] {
  try {
    return validate(fileName, text);
  } catch (e) {
    return [`${fileName}: $ internal error: ${e instanceof Error ? e.message : String(e)}`];
  }
}

function validate(fileName: string, text: string): string[] {
  if (textDepth(text) > MAX_TEXT_DEPTH) {
    return [`${fileName}: $ is nested more than ${MAX_TEXT_DEPTH} levels deep`];
  }
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch (e) {
    return [`${fileName}: invalid JSON: ${e instanceof Error ? e.message : String(e)}`];
  }
  const problems: string[] = [];
  const report: Report = (path, message) => {
    problems.push(`${fileName}: ${path} ${message}`);
  };

  if (!isObject(value)) return [`${fileName}: $ must be an object`];

  const scan = scanText(text);
  const ctx: Ctx = {
    report,
    keys: (obj, kp) => scan.objectKeys.get(kp) ?? Object.keys(obj),
  };

  const kind = get(value, 'kind');
  const validKind = kind === 'reducer' || kind === 'canonical_json';
  if (!has(value, 'kind')) report('kind', 'is required');
  else if (!validKind) report('kind', 'must be "reducer" or "canonical_json"');

  if (!has(value, 'format')) report('format', 'is required');
  else if (value.format !== VECTOR_FORMAT) report('format', 'must be the integer 1');

  const stem = fileName.replace(/\.json$/, '');
  if (!has(value, 'name')) report('name', 'is required');
  else if (value.name !== stem) report('name', `must equal "${stem}"`);

  if (!has(value, 'description')) report('description', 'is required');
  else if (!isNonEmptyString(value.description))
    report('description', 'must be a non-empty string');

  if (kind === 'reducer') validateReducer(value, ctx);
  else if (kind === 'canonical_json') validateCanonical(value, ctx);

  if (validKind) {
    const allowed = kind === 'reducer' ? REDUCER_KEYS : CANONICAL_KEYS;
    for (const key of ctx.keys(value, '')) {
      if (!allowed.includes(key)) report(key, 'is not a known key');
    }
  }

  for (const { path, token } of scan.tokens) {
    const inEvents = path.startsWith('events[');
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

function requireKey(obj: Obj, key: string, path: string, report: Report): boolean {
  if (has(obj, key)) return true;
  report(path, 'is required');
  return false;
}

function validateReducer(vector: Obj, ctx: Ctx): void {
  const { report } = ctx;
  if (requireKey(vector, 'game_id', 'game_id', report) && !isNonEmptyString(vector.game_id)) {
    report('game_id', 'must be a non-empty string');
  }

  if (requireKey(vector, 'settings', 'settings', report)) {
    const settings = vector.settings;
    if (!isObject(settings)) report('settings', 'must be an object');
    else {
      for (const key of ctx.keys(settings, keyStep('', 'settings'))) {
        if (!SETTING_NAMES.has(key)) report(`settings.${key}`, 'is not a known key');
      }
    }
  }

  const events = get(vector, 'events');
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

  // The events that are applied: those below a valid `expect.error.index`, otherwise all of them.
  let limit = eventCount ?? 0;
  if (requireKey(vector, 'expect', 'expect', report)) {
    const expectValue = vector.expect;
    if (!isObject(expectValue)) report('expect', 'must be an object');
    else {
      const limitFromExpect = validateExpect(expectValue, eventCount, ctx);
      if (limitFromExpect !== null) limit = limitFromExpect;
    }
  }

  // Events before `limit` are applied, so each needs a string `type`; the rejected event and the
  // ones after it may be malformed on purpose (the `reject-type-*` vectors).
  if (Array.isArray(events)) {
    events.slice(0, limit).forEach((event: unknown, i) => {
      if (isObject(event) && typeof get(event, 'type') !== 'string') {
        report(`events[${i}].type`, 'must be a string');
      }
    });
  }
}

/** Checks `expect`; returns the index below which events are applied when it is a valid index. */
function validateExpect(expectValue: Obj, eventCount: number | null, ctx: Ctx): number | null {
  const { report } = ctx;
  const expectKp = keyStep('', 'expect');
  for (const key of ctx.keys(expectValue, expectKp)) {
    if (key !== 'state' && key !== 'error') report(`expect.${key}`, 'is not a known key');
  }
  const hasState = has(expectValue, 'state');
  const hasError = has(expectValue, 'error');
  if (hasState === hasError) report('expect', 'must have exactly one of "state" or "error"');
  if (hasState && !isObject(expectValue.state)) report('expect.state', 'must be an object');

  let limit: number | null = null;
  if (hasError) {
    const error = expectValue.error;
    if (!isObject(error)) report('expect.error', 'must be an object');
    else {
      for (const key of ctx.keys(error, keyStep(expectKp, 'error'))) {
        if (key !== 'index' && key !== 'code') report(`expect.error.${key}`, 'is not a known key');
      }
      if (requireKey(error, 'index', 'expect.error.index', report)) {
        const index = error.index;
        if (!isNonNegativeInteger(index)) {
          report('expect.error.index', 'must be a non-negative integer');
        } else if (eventCount !== null && index >= eventCount) {
          report('expect.error.index', `must be less than the number of events (${eventCount})`);
        } else if (eventCount !== null) limit = index;
      }
      if (requireKey(error, 'code', 'expect.error.code', report)) {
        const code = error.code;
        if (
          typeof code !== 'string' ||
          !(REDUCER_ERROR_CODES as readonly string[]).includes(code)
        ) {
          report('expect.error.code', 'must be a known reducer error code');
        }
      }
    }
  }
  return limit;
}

function validateCanonical(vector: Obj, ctx: Ctx): void {
  const { report } = ctx;
  if (!requireKey(vector, 'cases', 'cases', report)) return;
  const cases = vector.cases;
  if (!Array.isArray(cases) || cases.length === 0) {
    report('cases', 'must be a non-empty array');
    return;
  }
  const names = new Map<string, number>();
  cases.forEach((c: unknown, i) => {
    const at = `cases[${i}]`;
    const kp = keyStep(keyStep('', 'cases'), i);
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

    const hasInput = has(c, 'input');
    const hasInvalid = has(c, 'invalid');
    if (hasInput === hasInvalid) {
      report(at, 'must have exactly one of "input" or "invalid"');
      return;
    }
    const allowed = hasInput ? ['name', 'input', 'output'] : ['name', 'invalid', 'error'];
    for (const key of ctx.keys(c, kp)) {
      if (!allowed.includes(key)) report(`${at}.${key}`, 'is not a known key');
    }
    if (hasInput) {
      if (requireKey(c, 'output', `${at}.output`, report) && typeof c.output !== 'string') {
        report(`${at}.output`, 'must be a string');
      }
    } else {
      validateInvalidCase(c, at, kp, ctx);
    }
  });
}

function validateInvalidCase(c: Obj, at: string, kp: string, ctx: Ctx): void {
  const { report } = ctx;
  if (requireKey(c, 'error', `${at}.error`, report)) {
    const error = c.error;
    if (
      typeof error !== 'string' ||
      !(CANONICAL_ERROR_CODES as readonly string[]).includes(error)
    ) {
      report(`${at}.error`, 'must be a canonical JSON error code');
    }
  }
  const invalid = c.invalid;
  if (!isObject(invalid)) {
    report(`${at}.invalid`, 'must be an object');
    return;
  }
  for (const key of ctx.keys(invalid, keyStep(kp, 'invalid'))) {
    if (key !== 'kind' && key !== 'value') report(`${at}.invalid.${key}`, 'is not a known key');
  }
  if (!has(invalid, 'kind')) {
    report(`${at}.invalid.kind`, 'is required');
    return;
  }
  const kind = invalid.kind;
  if (typeof kind !== 'string' || !(INVALID_KINDS as readonly string[]).includes(kind)) {
    report(`${at}.invalid.kind`, `must be one of ${INVALID_KINDS.join(', ')}`);
    return;
  }
  const path = `${at}.invalid.value`;
  if (kind === 'nan' || kind === 'infinity') {
    if (has(invalid, 'value')) report(path, 'must be absent');
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
    const digits = text.replace('-', '').length;
    if (!ok) {
      report(path, 'must be decimal text of an integer outside the safe range');
    } else if (digits > MAX_INTEGER_DIGITS) {
      report(path, `must have at most ${MAX_INTEGER_DIGITS} digits`);
    } else if ((BigInt(text) < 0n ? -BigInt(text) : BigInt(text)) <= MAX_SAFE) {
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
 * skipped here (it fails its own test), and so is a file that cannot be processed: one broken file
 * never aborts the check. Returns one message per uncovered type, in registry order.
 */
export function checkCoverage(
  files: readonly { name: string; text: string }[],
  registry: readonly string[] = EVENT_TYPES,
): string[] {
  const seen = new Set<string>();
  for (const { name, text } of files) {
    try {
      if (validateVectorText(name, text).length > 0) continue;
      for (const type of exercisedEventTypes(JSON.parse(text) as Obj)) seen.add(type);
    } catch {
      continue;
    }
  }
  return registry.filter((type) => !seen.has(type)).map(uncoveredMessage);
}
