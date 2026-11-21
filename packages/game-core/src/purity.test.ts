import { mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
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
  ['globalThis (dot or bracket access)', /\bglobalThis\b/],
  ['self', /\bself\b/],
  ['window', /\bwindow\b/],
  ['eval', /\beval\b/],
  ['Function constructor', /\bFunction\s*\(/],
  ['dynamic import()', /\bimport\s*\(/],
];

/** One snippet per pattern that must be caught (positive controls for the scan below). */
const CONTROLS: readonly [string, string][] = [
  ['Date', 'const t = new Date();'],
  ['Math.random', 'const r = Math.random();'],
  ['performance', 'const t = performance.now();'],
  ['crypto', 'crypto.getRandomValues(a);'],
  ['fetch', 'await fetch(url);'],
  ['XMLHttpRequest / WebSocket', 'new WebSocket(url);'],
  ['timers', 'setTimeout(f, 1);'],
  ['process / require', 'const e = process.env.X;'],
  ['Intl / toLocale*', 'n.toLocaleString();'],
  ['localStorage / sessionStorage', 'localStorage.getItem(k);'],
  ['node: imports', "import { readFileSync } from 'node:fs';"],
  ['globalThis (dot or bracket access)', "const d = globalThis['Da' + 'te'];"],
  ['globalThis (dot or bracket access)', 'const d = globalThis.Date;'],
  ['self', 'const d = self.Date;'],
  ['window', 'const d = window.Date;'],
  ['eval', "eval('1');"],
  ['Function constructor', "const f = Function('return 1');"],
  ['Function constructor', "const f = new Function ('return 1');"],
  ['dynamic import()', "const m = await import('./x');"],
];

const dir = new URL('./', import.meta.url);
const SEP = String.fromCharCode(92);

/** Every non-test source file below `root`, at any depth, as a slash-separated relative path. */
function listSources(root: URL | string): string[] {
  return (readdirSync(root, { recursive: true }) as string[])
    .map((f) => f.split(SEP).join('/'))
    .filter((f) => /[.](ts|tsx|js|mjs|cjs)$/.test(f) && !/[.]test[.]/.test(f));
}

const sources = listSources(dir);

function stripComments(text: string): string {
  return text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
}

function violations(text: string): string[] {
  const code = stripComments(text);
  return FORBIDDEN.filter(([, pattern]) => pattern.test(code)).map(([name]) => name);
}

describe('game-core purity guard', () => {
  it('finds the source files', () => {
    expect(sources).toEqual(expect.arrayContaining(['reducer.ts', 'canonical-json.ts']));
  });

  it('scans subdirectories too', () => {
    const root = mkdtempSync(join(tmpdir(), 'purity-'));
    try {
      mkdirSync(join(root, 'a', 'b'), { recursive: true });
      writeFileSync(join(root, 'a', 'b', 'deep.ts'), 'export const t = Date.now();');
      writeFileSync(join(root, 'top.ts'), 'export const x = 1;');
      writeFileSync(join(root, 'top.test.ts'), 'export const y = 2;');
      expect(listSources(root).sort()).toEqual(['a/b/deep.ts', 'top.ts']);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it.each(CONTROLS)('flags %s in: %s', (name, snippet) => {
    expect(violations(snippet)).toContain(name);
  });

  it('ignores a forbidden word inside a comment', () => {
    expect(violations('// uses Date.now() and eval\n/* window self */ const a = 1;')).toEqual([]);
  });

  it.each(sources)('%s uses no clock, randomness or I/O', (file) => {
    const text = readFileSync(new URL(file, dir), 'utf8');
    expect(violations(text), `${file} must not use these`).toEqual([]);
  });
});
