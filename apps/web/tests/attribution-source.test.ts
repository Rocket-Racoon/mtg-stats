// @vitest-environment node
// S-1.6.6 AC3 (anti-drift). Static analysis: the wording lives only in packages/card-utils; web
// must import it. The footer CSS checks (AC4) are in footer-css.test.ts.
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join, sep } from 'node:path';
import { describe, expect, it } from 'vitest';

const srcDir = fileURLToPath(new URL('../src', import.meta.url));

function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? walk(join(dir, e.name)) : [join(dir, e.name)],
  );
}
const sources = walk(srcDir)
  .filter((f) => /\.tsx?$/.test(f) && !/\.test\.tsx?$/.test(f))
  .map((f) => ({ f: f.split(sep).join('/'), text: readFileSync(f, 'utf8') }));

const stripComments = (s: string) =>
  s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');

describe('notice wording is not duplicated in web source (AC3)', () => {
  it('scans a non-empty set of source files', () => {
    expect(sources.length).toBeGreaterThan(5);
  });

  it.each([
    'Fan Content Policy',
    'courtesy of',
    'Wizards of the Coast',
    'unofficial Fan Content',
    'approved/endorsed',
    'company.wizards.com',
    'scryfall.com',
  ])('no web source file contains the literal %s outside comments', (fragment) => {
    const offenders = sources.filter(({ text }) => stripComments(text).includes(fragment));
    expect(offenders.map(({ f }) => f)).toEqual([]);
  });

  it('the Attribution and About components import from @mtg-stats/card-utils', () => {
    for (const name of ['Attribution.tsx', 'pages/About.tsx']) {
      const text = sources.find(({ f }) => f.endsWith(`/src/${name}`))?.text;
      expect(text, name).toMatch(/from '@mtg-stats\/card-utils'/);
    }
  });
});
