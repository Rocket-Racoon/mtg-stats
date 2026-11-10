// @vitest-environment node
// S-1.6.6 AC3 (anti-drift) and AC4 (CSS level). Limits: this is static analysis. The wording lives
// only in packages/card-utils; web must import it. The real-browser 360px "no horizontal scroll"
// measurement is S-1.8.5's (Playwright); jsdom cannot lay out.
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join, sep } from 'node:path';
import { describe, expect, it } from 'vitest';
import { CONTRAST_PAIRS } from '../src/theme/tokens';

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

const css = readFileSync(join(srcDir, 'App.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
const rules = [...css.matchAll(/([^{}@]+)\{([^}]*)\}/g)].map((m) => ({
  selector: (m[1] ?? '').trim(),
  body: m[2] ?? '',
}));
const footerRules = rules.filter(({ selector }) =>
  selector
    .split(',')
    .some((s) => /(^|\s)\.(shell-footer|attribution|footer-controls|footer-link)\b/.test(s.trim())),
);

describe('footer CSS cannot force overflow at 360px (AC4)', () => {
  it('finds the footer rules (guards the parser)', () => {
    expect(footerRules.length).toBeGreaterThanOrEqual(4);
  });

  it('no footer rule uses nowrap or pre', () => {
    for (const { selector, body } of footerRules) {
      expect(body, selector).not.toMatch(/white-space:\s*(nowrap|pre)/);
    }
  });

  it('no footer rule sets a fixed or minimum width', () => {
    for (const { selector, body } of footerRules) {
      const widths = [...body.matchAll(/(?:^|[;\s])(?:min-)?width\s*:\s*([^;]+)/g)].map((m) =>
        (m[1] ?? '').trim(),
      );
      for (const w of widths) expect(w, selector).toMatch(/^(0|auto|100%)$/);
    }
  });

  it('the footer wraps its children', () => {
    expect(footerRules.find((r) => r.selector === '.shell-footer')?.body).toMatch(
      /flex-wrap:\s*wrap/,
    );
  });

  it('long words and URLs can break in the notice', () => {
    expect(footerRules.find((r) => r.selector === '.attribution')?.body).toMatch(
      /overflow-wrap:\s*(anywhere|break-word)/,
    );
  });

  it('footer colours come only from tokens, and the footer pair is in CONTRAST_PAIRS', () => {
    for (const { selector, body } of footerRules) {
      for (const m of body.matchAll(/(?:^|[;\s])(color|background(?:-color)?)\s*:\s*([^;]+)/g)) {
        expect(m[2]?.trim(), `${selector} ${m[1]}`).toMatch(/^var\(--[a-z-]+\)$/);
      }
    }
    const footer = footerRules.find((r) => r.selector === '.shell-footer');
    expect(footer?.body).toMatch(/color:\s*var\(--muted-fg\)/);
    expect(footer?.body).toMatch(/background:\s*var\(--muted-bg\)/);
    expect(CONTRAST_PAIRS.some((p) => p.fg === 'mutedFg' && p.bg === 'mutedBg')).toBe(true);
  });
});
