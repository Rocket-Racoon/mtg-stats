/**
 * Canonical JSON serializer (CONTRACT.md section 5).
 *
 * Pure and locale-independent: no Intl, no toLocaleString, no JSON.stringify.
 * The Python implementation must produce byte-identical output.
 */

export type CanonicalJsonErrorCode =
  'non_integer' | 'integer_out_of_range' | 'lone_surrogate' | 'unsupported_value';

export class CanonicalJsonError extends Error {
  readonly code: CanonicalJsonErrorCode;

  constructor(code: CanonicalJsonErrorCode, message: string) {
    super(`${code}: ${message}`);
    this.name = 'CanonicalJsonError';
    this.code = code;
  }
}

/** Largest integer magnitude allowed: 2^53 - 1 (JavaScript's Number.MAX_SAFE_INTEGER). */
export const MAX_SAFE_INTEGER = 9007199254740991;
/** A container with more than this many ancestors is rejected; with this many or fewer it is allowed. */
export const MAX_DEPTH = 64;

/** A value the canonical serializer accepts. */
export type CanonicalValue =
  | null
  | boolean
  | number
  | string
  | readonly CanonicalValue[]
  | { readonly [key: string]: CanonicalValue };

/** One backslash character, spelled by code so no source escaping is involved. */
const BS = String.fromCharCode(0x5c);

const SHORT_ESCAPES: Readonly<Record<number, string>> = {
  0x08: BS + 'b',
  0x09: BS + 't',
  0x0a: BS + 'n',
  0x0c: BS + 'f',
  0x0d: BS + 'r',
};

function serializeString(s: string): string {
  let out = '"';
  for (let i = 0; i < s.length; i++) {
    const unit = s.charCodeAt(i);
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = i + 1 < s.length ? s.charCodeAt(i + 1) : 0;
      if (next < 0xdc00 || next > 0xdfff) {
        throw new CanonicalJsonError('lone_surrogate', 'string contains a lone high surrogate');
      }
      out += s[i]! + s[i + 1]!;
      i++;
    } else if (unit >= 0xdc00 && unit <= 0xdfff) {
      throw new CanonicalJsonError('lone_surrogate', 'string contains a lone low surrogate');
    } else if (unit === 0x22) {
      out += BS + '"';
    } else if (unit === 0x5c) {
      out += BS + BS;
    } else if (unit < 0x20) {
      out += SHORT_ESCAPES[unit] ?? BS + 'u' + unit.toString(16).padStart(4, '0');
    } else {
      out += s[i]!;
    }
  }
  return out + '"';
}

/** Compare two strings by Unicode code point (equals UTF-8 byte order), not UTF-16 code unit. */
export function compareCodePoints(a: string, b: string): number {
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    const ca = a.codePointAt(i)!;
    const cb = b.codePointAt(j)!;
    if (ca !== cb) return ca < cb ? -1 : 1;
    i += ca > 0xffff ? 2 : 1;
    j += cb > 0xffff ? 2 : 1;
  }
  if (i < a.length) return 1;
  if (j < b.length) return -1;
  return 0;
}

function serializeInteger(n: number): string {
  if (!Number.isInteger(n)) {
    throw new CanonicalJsonError('non_integer', `${String(n)} is not an integer`);
  }
  if (Math.abs(n) > MAX_SAFE_INTEGER) {
    throw new CanonicalJsonError('integer_out_of_range', `${String(n)} exceeds +-(2^53 - 1)`);
  }
  return n === 0 ? '0' : String(n); // -0 serializes as "0"
}

function isPlainObject(v: object): boolean {
  const proto = Object.getPrototypeOf(v) as unknown;
  return proto === Object.prototype || proto === null;
}

function serialize(value: unknown, depth: number): string {
  if (value === null) return 'null';
  switch (typeof value) {
    case 'boolean':
      return value ? 'true' : 'false';
    case 'number':
      return serializeInteger(value);
    case 'string':
      return serializeString(value);
    case 'object':
      break;
    default:
      throw new CanonicalJsonError('unsupported_value', `unsupported type ${typeof value}`);
  }
  if (depth > MAX_DEPTH) {
    throw new CanonicalJsonError('unsupported_value', `nesting deeper than ${MAX_DEPTH}`);
  }
  if (Array.isArray(value)) {
    const parts: string[] = [];
    for (let i = 0; i < value.length; i++) {
      parts.push(serialize((value as unknown[])[i], depth + 1));
    }
    return `[${parts.join(',')}]`;
  }
  if (!isPlainObject(value)) {
    throw new CanonicalJsonError('unsupported_value', 'only plain objects and arrays are allowed');
  }
  const keys = Object.keys(value).sort(compareCodePoints);
  const parts = keys.map(
    (key) =>
      `${serializeString(key)}:${serialize((value as Record<string, unknown>)[key], depth + 1)}`,
  );
  return `{${parts.join(',')}}`;
}

/** Serialize to canonical JSON text. The bytes on the wire are this string encoded as UTF-8. */
export function canonicalJson(value: unknown): string {
  return serialize(value, 0);
}

/** Canonical JSON encoded as UTF-8 bytes, without a byte order mark. */
export function canonicalJsonBytes(value: unknown): Uint8Array {
  const bytes: number[] = [];
  for (const ch of canonicalJson(value)) {
    const cp = ch.codePointAt(0)!;
    if (cp < 0x80) {
      bytes.push(cp);
    } else if (cp < 0x800) {
      bytes.push(0xc0 | (cp >> 6), 0x80 | (cp & 0x3f));
    } else if (cp < 0x10000) {
      bytes.push(0xe0 | (cp >> 12), 0x80 | ((cp >> 6) & 0x3f), 0x80 | (cp & 0x3f));
    } else {
      bytes.push(
        0xf0 | (cp >> 18),
        0x80 | ((cp >> 12) & 0x3f),
        0x80 | ((cp >> 6) & 0x3f),
        0x80 | (cp & 0x3f),
      );
    }
  }
  return Uint8Array.from(bytes);
}
