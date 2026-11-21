import { readdirSync, readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

/** CONTRACT.md section 7: non-test sources read no clock, randomness or I/O. */
const FORBIDDEN: readonly [string, RegExp][] = [
  ['Date', /\bDate\b/],
  ['Math.random', /\bMath\s*\.\s*random\b/],
  ['performance', /\bperformance\b/],
  ['crypto', /\bcrypto\b/],
  ['fetch', /\bfetch\b/],
  ['XMLHttpRequest / WebSocket', /\b(XMLHttpRequest|WebSocket)\b/],
  ['timers', /\b(setTimeout|setInterval|setImmediate|queueMicrotask)\b/],
  ['process / require', /\b(process|require)\b/],
  ['Intl / toLocale*', /\b(Intl|toLocale\w*)\b/],
  ['localStorage / sessionStorage', /\b(localStorage|sessionStorage)\b/],
  ['node: imports', /from\s+['"]node:/],
];

const dir = new URL('./', import.meta.url);
const sources = readdirSync(dir).filter((f) => f.endsWith('.ts') && !f.endsWith('.test.ts'));

function stripComments(text: string): string {
  return text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
}

describe('game-core purity guard', () => {
  it('finds the source files', () => {
    expect(sources).toEqual(expect.arrayContaining(['reducer.ts', 'canonical-json.ts']));
  });

  it.each(sources)('%s uses no clock, randomness or I/O', (file) => {
    const text = stripComments(readFileSync(new URL(file, dir), 'utf8'));
    for (const [name, pattern] of FORBIDDEN) {
      expect(pattern.test(text), `${file} must not use ${name}`).toBe(false);
    }
  });
});
