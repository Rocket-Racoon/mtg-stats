import { readFileSync } from 'node:fs';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { CanonicalJsonError, canonicalJson, canonicalJsonBytes } from './canonical-json';

/**
 * Acceptance criterion 2 (S-6.1.1), TypeScript side, independent of the Python files: the
 * serializer is compared with a reference written here from CONTRACT.md section 5 using only
 * JSON.stringify for scalars and a UTF-8 byte comparison for key order.
 */

type Json = null | boolean | number | string | Json[] | { [key: string]: Json };

function utf8Compare(a: string, b: string): number {
  return Buffer.compare(Buffer.from(a, 'utf8'), Buffer.from(b, 'utf8'));
}

function reference(value: Json): string {
  if (value === null) return 'null';
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (typeof value === 'number') return BigInt(value).toString();
  if (typeof value === 'string') return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(reference).join(',')}]`;
  const keys = Object.keys(value).sort(utf8Compare);
  return `{${keys.map((k) => `${JSON.stringify(k)}:${reference(value[k]!)}`).join(',')}}`;
}

/** Deterministic PRNG (mulberry32) so a failure reproduces. */
function prng(seed: number): () => number {
  let a = seed;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// Ranges chosen so UTF-16 order and code point order disagree, plus every escape class.
const RANGES: readonly [number, number][] = [
  [0x00, 0x1f],
  [0x20, 0x7e],
  [0x7f, 0x7f],
  [0x80, 0x9f],
  [0xa0, 0x7ff],
  [0x800, 0xd7ff],
  [0xe000, 0xffff],
  [0x10000, 0x10ffff],
];

function randomString(rand: () => number, maxLen: number): string {
  const len = Math.floor(rand() * (maxLen + 1));
  let s = '';
  for (let i = 0; i < len; i++) {
    const roll = rand();
    if (roll < 0.1) s += ['"', '\\', '/'][Math.floor(rand() * 3)]!;
    else {
      const [lo, hi] = RANGES[Math.floor(rand() * RANGES.length)]!;
      s += String.fromCodePoint(lo + Math.floor(rand() * (hi - lo + 1)));
    }
  }
  return s;
}

function randomInt(rand: () => number): number {
  const choices = [0, 1, -1, 9007199254740991, -9007199254740991, 2 ** 31, -(2 ** 31), 2 ** 52];
  if (rand() < 0.3) return choices[Math.floor(rand() * choices.length)]!;
  const magnitude = Math.floor(rand() * 53);
  return Math.floor((rand() * 2 - 1) * 2 ** magnitude);
}

function randomValue(rand: () => number, depth: number): Json {
  const roll = rand();
  if (depth <= 0 || roll < 0.45) {
    const scalar = rand();
    if (scalar < 0.15) return null;
    if (scalar < 0.3) return rand() < 0.5;
    if (scalar < 0.55) return randomInt(rand);
    return randomString(rand, 8);
  }
  if (roll < 0.7)
    return Array.from({ length: Math.floor(rand() * 5) }, () => randomValue(rand, depth - 1));
  const obj: { [key: string]: Json } = {};
  for (let i = Math.floor(rand() * 6); i > 0; i--)
    obj[randomString(rand, 5)] = randomValue(rand, depth - 1);
  return obj;
}

describe('canonical JSON against an independent reference (criterion 2)', () => {
  it('matches the reference text and UTF-8 bytes for 2000 generated values', () => {
    const rand = prng(611);
    for (let i = 0; i < 2000; i++) {
      const value = randomValue(rand, 5);
      const expected = reference(value);
      expect(canonicalJson(value)).toBe(expected);
      expect(Buffer.from(canonicalJsonBytes(value)).equals(Buffer.from(expected, 'utf8'))).toBe(
        true,
      );
    }
  });

  it('matches the reference for generated key sets that mix BMP-high and astral keys', () => {
    const rand = prng(53);
    for (let i = 0; i < 300; i++) {
      const obj: { [key: string]: Json } = {};
      for (let k = 0; k < 8; k++) {
        const [lo, hi] = RANGES[5 + Math.floor(rand() * 3)]!;
        obj[String.fromCodePoint(lo + Math.floor(rand() * (hi - lo + 1)))] = k;
      }
      expect(canonicalJson(obj)).toBe(reference(obj));
    }
  });

  it('has expected outputs in the shared vectors that equal the reference text', () => {
    const dir = new URL('../vectors/', import.meta.url);
    const names = [
      'canonical-json-key-order',
      'canonical-json-strings',
      'canonical-json-integers',
      'canonical-json-structure',
      'canonical-json-game-state',
    ];
    for (const name of names) {
      const vector = JSON.parse(readFileSync(new URL(`${name}.json`, dir), 'utf8')) as {
        cases: { name: string; input: Json; output: string }[];
      };
      expect(vector.cases.length).toBeGreaterThanOrEqual(3);
      for (const c of vector.cases) {
        expect(reference(c.input), `${name}: ${c.name}`).toBe(c.output);
      }
    }
  });

  it('writes every integer power of ten and of two in range without an exponent or sign', () => {
    const values: number[] = [];
    for (let e = 0; e <= 15; e++) values.push(10 ** e, -(10 ** e));
    for (let e = 0; e <= 52; e++) values.push(2 ** e, -(2 ** e), 2 ** e - 1);
    values.push(9007199254740991, -9007199254740991);
    for (const n of values) {
      expect(canonicalJson(n)).toBe(BigInt(n).toString());
    }
  });

  it('is independent of the insertion order of keys', () => {
    const a = { b: 1, a: 2, [String.fromCodePoint(0x10000)]: 3, '～': 4, '': 5, '10': 6, '9': 7 };
    const keys = Object.keys(a).reverse();
    const b: Record<string, number> = {};
    for (const k of keys) b[k] = (a as Record<string, number>)[k]!;
    expect(canonicalJson(b)).toBe(canonicalJson(a));
  });
});

describe('canonical JSON does not depend on locale (criterion 2)', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  function trapLocaleApis(): void {
    const boom = (name: string) => () => {
      throw new Error(`locale-sensitive API used: ${name}`);
    };
    vi.spyOn(String.prototype, 'localeCompare').mockImplementation(boom('localeCompare'));
    vi.spyOn(String.prototype, 'toLocaleLowerCase').mockImplementation(boom('toLocaleLowerCase'));
    vi.spyOn(String.prototype, 'toLocaleUpperCase').mockImplementation(boom('toLocaleUpperCase'));
    vi.spyOn(Number.prototype, 'toLocaleString').mockImplementation(boom('Number toLocaleString'));
    vi.spyOn(Array.prototype, 'toLocaleString').mockImplementation(boom('Array toLocaleString'));
    vi.spyOn(Intl.Collator.prototype, 'compare', 'get').mockImplementation(boom('Intl.Collator'));
    vi.spyOn(
      Intl.NumberFormat.prototype as unknown as { format: string },
      'format',
      'get',
    ).mockImplementation(boom('Intl.NumberFormat'));
    vi.spyOn(String.prototype, 'normalize').mockImplementation(boom('normalize'));
  }

  const sample: Json = {
    z: [1, -2, 9007199254740991],
    I: 'i̇ İ ß é é',
    '\u{10000}': { '～': 'Zoë 日本', a: null },
    '10': true,
    '9': 1234567,
  };

  it('has working traps (a locale-sensitive call throws while they are installed)', () => {
    trapLocaleApis();
    expect(() => 'a'.localeCompare('b')).toThrow(/locale-sensitive/);
    expect(() => (1234).toLocaleString()).toThrow(/locale-sensitive/);
    expect(() => new Intl.Collator().compare('a', 'b')).toThrow(/locale-sensitive/);
    expect(() => 'é'.normalize()).toThrow(/locale-sensitive/);
  });

  it('serializes without calling localeCompare, toLocale*, Intl or normalize', () => {
    const expected = canonicalJson(sample);
    trapLocaleApis();
    let actual = '';
    expect(() => {
      actual = canonicalJson(sample);
    }).not.toThrow();
    expect(actual).toBe(expected);
    expect(actual).toBe(reference(sample));
  });

  it('writes numbers with ASCII digits and no grouping separators', () => {
    trapLocaleApis();
    expect(canonicalJson([1234567, -1234567, 9007199254740991])).toBe(
      '[1234567,-1234567,9007199254740991]',
    );
  });
});

describe('canonical JSON rejections (criterion 2: integers only, no floats)', () => {
  const codeOf = (fn: () => unknown): string | undefined => {
    try {
      fn();
    } catch (e) {
      return e instanceof CanonicalJsonError ? e.code : `other: ${String(e)}`;
    }
    return undefined;
  };

  it.each([
    [0.1 + 0.2],
    [-0.5],
    [Number.MIN_VALUE],
    [Number.EPSILON],
    [Number.NaN],
    [Number.POSITIVE_INFINITY],
    [Number.NEGATIVE_INFINITY],
  ])('rejects the number %s with non_integer', (n) => {
    expect(codeOf(() => canonicalJson(n))).toBe('non_integer');
    expect(codeOf(() => canonicalJson({ a: [n] }))).toBe('non_integer');
  });

  it.each([[2 ** 53], [-(2 ** 53)], [2 ** 63], [1e21], [1e300]])(
    'rejects the integer-valued number %s with integer_out_of_range',
    (n) => {
      // JavaScript cannot tell 1e300 from an integer; contract 5.4 gives integers out of range.
      expect(codeOf(() => canonicalJson(n))).toBe('integer_out_of_range');
    },
  );

  it('writes -0 as 0 (the only way a number can differ from its text)', () => {
    expect(canonicalJson(-0)).toBe('0');
    expect(canonicalJson([-0, { a: -0 }])).toBe('[0,{"a":0}]');
  });

  it.each([
    ['a high surrogate followed by a high surrogate', '\ud83d\ud83d'],
    ['a low surrogate then a high surrogate', '\ude00\ud83d'],
    ['a trailing high surrogate', 'a\ud83d'],
    ['a leading low surrogate', '\ude00a'],
  ])('rejects %s in a value and in a key', (_name, text) => {
    expect(codeOf(() => canonicalJson(text))).toBe('lone_surrogate');
    expect(codeOf(() => canonicalJson({ [text]: 1 }))).toBe('lone_surrogate');
  });

  it('accepts a valid surrogate pair', () => {
    expect(canonicalJson('😀')).toBe('"\u{1f600}"');
  });

  it.each([
    ['undefined', undefined],
    ['a hole in an array', [1, , 3]], // eslint-disable-line no-sparse-arrays
    ['an undefined property', { a: undefined }],
    ['a symbol', Symbol('x')],
    ['a bigint', 5n],
    ['a function', () => 1],
    ['a Date', new Date(0)],
    ['a Map', new Map()],
    ['a Set', new Set()],
    ['a class instance', new (class Foo {})()],
    ['a typed array', new Uint8Array(1)],
  ])('rejects %s with unsupported_value', (_name, value) => {
    expect(codeOf(() => canonicalJson(value))).toBe('unsupported_value');
  });

  it('enforces the nesting limit for arrays, objects and a scalar at the bottom', () => {
    const arrays = (n: number, bottom: unknown): unknown => {
      let v = bottom;
      for (let i = 0; i < n; i++) v = [v];
      return v;
    };
    const objects = (n: number, bottom: unknown): unknown => {
      let v = bottom;
      for (let i = 0; i < n; i++) v = { a: v };
      return v;
    };
    expect(codeOf(() => canonicalJson(arrays(65, 1)))).toBeUndefined();
    expect(codeOf(() => canonicalJson(arrays(66, 1)))).toBe('unsupported_value');
    // With an empty container at the bottom, n wraps make n + 1 nested containers.
    expect(codeOf(() => canonicalJson(arrays(64, [])))).toBeUndefined();
    expect(codeOf(() => canonicalJson(arrays(65, [])))).toBe('unsupported_value');
    expect(codeOf(() => canonicalJson(objects(64, {})))).toBeUndefined();
    expect(codeOf(() => canonicalJson(objects(65, {})))).toBe('unsupported_value');
    expect(codeOf(() => canonicalJson({ a: arrays(65, 1) }))).toBe('unsupported_value');
    expect(codeOf(() => canonicalJson(arrays(5000, 1)))).toBe('unsupported_value');
  });
});
