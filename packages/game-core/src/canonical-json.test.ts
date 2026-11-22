import { describe, expect, it } from 'vitest';
import {
  CanonicalJsonError,
  canonicalJson,
  canonicalJsonBytes,
  compareCodePoints,
  MAX_DEPTH,
} from './canonical-json';

const codeOf = (fn: () => unknown): string | undefined => {
  try {
    fn();
  } catch (e) {
    return e instanceof CanonicalJsonError ? e.code : 'other';
  }
  return undefined;
};

describe('canonicalJson', () => {
  it('sorts keys recursively and emits no whitespace', () => {
    expect(canonicalJson({ b: [1, { z: 1, a: 2 }], a: null })).toBe(
      '{"a":null,"b":[1,{"a":2,"z":1}]}',
    );
  });

  it('orders keys by code point, not UTF-16 code unit', () => {
    // U+10000 is the surrogate pair D800 DC00, which sorts before U+FF5E by code unit.
    expect(canonicalJson({ '\u{10000}': 1, '～': 2 })).toBe('{"～":2,"\u{10000}":1}');
    expect(compareCodePoints('\u{10000}', '～')).toBe(1);
    expect(compareCodePoints('a', 'ab')).toBe(-1);
    expect(compareCodePoints('ab', 'ab')).toBe(0);
  });

  it('escapes only quote, backslash and C0 controls', () => {
    expect(canonicalJson('"\\\b\t\n\f\r\u0000\u001f\u007f/ é')).toBe(
      '"\\"\\\\\\b\\t\\n\\f\\r\\u0000\\u001f\u007f/ é"',
    );
  });

  it('writes non-BMP characters raw and encodes UTF-8', () => {
    expect(canonicalJson('\u{1f600}')).toBe('"\u{1f600}"');
    expect([...canonicalJsonBytes('\u{1f600}')]).toEqual([0x22, 0xf0, 0x9f, 0x98, 0x80, 0x22]);
  });

  it('writes negative zero as 0 and accepts the safe-integer bounds', () => {
    expect(canonicalJson(-0)).toBe('0');
    expect(canonicalJson([9007199254740991, -9007199254740991])).toBe(
      '[9007199254740991,-9007199254740991]',
    );
  });

  it.each([
    [1.5, 'non_integer'],
    [Number.NaN, 'non_integer'],
    [Number.POSITIVE_INFINITY, 'non_integer'],
    [9007199254740992, 'integer_out_of_range'],
    [-9007199254740992, 'integer_out_of_range'],
    [1e21, 'integer_out_of_range'],
    ['\ud800', 'lone_surrogate'],
    ['\udc00x', 'lone_surrogate'],
    ['\ud800x', 'lone_surrogate'],
    [undefined, 'unsupported_value'],
    [{ a: undefined }, 'unsupported_value'],
    [10n, 'unsupported_value'],
    [() => 1, 'unsupported_value'],
    [new Date(0), 'unsupported_value'],
    [new Map(), 'unsupported_value'],
  ])('rejects %s with %s', (value, code) => {
    expect(codeOf(() => canonicalJson(value))).toBe(code);
  });

  it('rejects a lone surrogate in a key', () => {
    expect(codeOf(() => canonicalJson({ '\ud800': 1 }))).toBe('lone_surrogate');
  });

  it('rejects nesting beyond the depth limit and accepts the limit itself', () => {
    const nest = (levels: number): unknown => {
      let v: unknown = 1;
      for (let i = 0; i < levels; i++) v = [v];
      return v;
    };
    expect(codeOf(() => canonicalJson(nest(MAX_DEPTH + 1)))).toBeUndefined();
    expect(codeOf(() => canonicalJson(nest(MAX_DEPTH + 2)))).toBe('unsupported_value');
  });

  it('encodes 2-byte and 3-byte UTF-8 sequences', () => {
    expect([...canonicalJsonBytes('é日')]).toEqual([0x22, 0xc3, 0xa9, 0xe6, 0x97, 0xa5, 0x22]);
  });

  it('accepts null-prototype objects', () => {
    const o = Object.assign(Object.create(null) as object, { b: 1, a: 2 });
    expect(canonicalJson(o)).toBe('{"a":2,"b":1}');
  });
});
