import { mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { loadVectorFiles, runVectorFile } from './vector-loader.test.helpers';
import { checkCoverage, validateVectorText } from './vector-validator';

/** Runner behavior of CONTRACT.md 8.3 (never throws) and 8.5 (discovery, canonical reporting). */

const canonical = (name: string, cases: unknown[]) =>
  JSON.stringify({ format: 1, kind: 'canonical_json', name, description: 'd', cases });

let dir: string;
beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'vector-loader-'));
});
afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

describe('discovery (8.5)', () => {
  const body = canonical('a', [{ name: 'x', input: 1, output: '1' }]);

  it('ignores a file whose extension is not exactly lower-case .json', () => {
    writeFileSync(join(dir, 'a.json'), body);
    writeFileSync(join(dir, 'b.JSON'), body);
    writeFileSync(join(dir, 'c.Json'), body);
    writeFileSync(join(dir, 'd.json.bak'), body);
    expect(loadVectorFiles(dir).map((f) => f.name)).toEqual(['a.json']);
  });

  it('fails loudly when only non-.json names are present', () => {
    writeFileSync(join(dir, 'b.JSON'), body);
    expect(() => loadVectorFiles(dir)).toThrow(/vectors directory has no \.json files/);
  });

  it('ignores a directory named like a vector and does not search nested directories', () => {
    writeFileSync(join(dir, 'a.json'), body);
    mkdirSync(join(dir, 'nested.json'));
    writeFileSync(join(dir, 'nested.json', 'inner.json'), body);
    expect(loadVectorFiles(dir).map((f) => f.name)).toEqual(['a.json']);
  });

  it('ignores symbolic links, including a dangling one, without an error', (ctx) => {
    writeFileSync(join(dir, 'a.json'), body);
    try {
      symlinkSync(join(dir, 'a.json'), join(dir, 'link.json'));
      symlinkSync(join(dir, 'missing-target'), join(dir, 'dangling.json'));
    } catch {
      ctx.skip(); // symbolic links need a privilege on some systems
    }
    expect(loadVectorFiles(dir).map((f) => f.name)).toEqual(['a.json']);
  });
});

describe('the validator never throws (8.3)', () => {
  const texts: [string, string][] = [
    ['100000 nested arrays', '['.repeat(100000) + ']'.repeat(100000)],
    ['unbalanced objects', '{"a":'.repeat(30000)],
    ['an unterminated string', '{"a":"' + '['.repeat(1000)],
    ['a huge number token', `{"a":${'9'.repeat(200000)}}`],
    ['empty text', ''],
    ['a lone brace', '}'],
  ];
  it.each(texts)('returns messages for %s', (_label, text) => {
    const messages = validateVectorText('x.json', text);
    expect(messages.length).toBeGreaterThan(0);
    expect(messages.every((m) => m.startsWith('x.json: '))).toBe(true);
  });

  it('keeps the coverage check going when a file is broken', () => {
    const files = [
      { name: 'deep.json', text: '['.repeat(100000) },
      { name: 'bad.json', text: '{' },
    ];
    expect(checkCoverage(files, ['alpha'])).toEqual(['uncovered event type: alpha']);
  });
});

describe('a canonical file reports every failing case (8.5)', () => {
  it('lists each failing case on its own line, in case order', () => {
    const text = canonical('a', [
      { name: 'one', input: [1], output: '[2]' },
      { name: 'fine', input: 1, output: '1' },
      { name: 'two', input: { b: 1, a: 2 }, output: 'wrong' },
      { name: 'three', invalid: { kind: 'nan' }, error: 'integer_out_of_range' },
    ]);
    let message = '';
    try {
      runVectorFile({ name: 'a.json', text });
    } catch (e) {
      message = (e as Error).message;
    }
    const lines = message.split('\n');
    expect(lines).toHaveLength(3);
    expect(lines[0]).toBe('a.json: case "one": expected [2], got [1]');
    expect(lines[1]).toMatch(/^a\.json: case "two": expected wrong, got /);
    expect(lines[2]).toMatch(/^a\.json: case "three": expected error integer_out_of_range, got /);
  });

  it('runs integer cases of 300 digits, positive and negative, as integer_out_of_range', () => {
    const text = canonical('a', [
      {
        name: 'big',
        invalid: { kind: 'integer', value: '9'.repeat(300) },
        error: 'integer_out_of_range',
      },
      {
        name: 'small',
        invalid: { kind: 'integer', value: '-' + '9'.repeat(300) },
        error: 'integer_out_of_range',
      },
    ]);
    expect(() => runVectorFile({ name: 'a.json', text })).not.toThrow();
  });

  it('names the file when a file is nested too deep', () => {
    const text = '['.repeat(600) + ']'.repeat(600);
    expect(() => runVectorFile({ name: 'a.json', text })).toThrow(
      'a.json: $ is nested more than 512 levels deep',
    );
  });
});
