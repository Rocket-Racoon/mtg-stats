// @vitest-environment node
// Static checks on the stylesheets (jsdom cannot lay out or paint). Real-browser verification of
// 44px targets and reduced motion belongs to S-1.8.5's real-browser acceptance criteria.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import {
  CONTRAST_PAIRS,
  THEMES,
  TOKENS,
  TOKEN_NAMES,
  kebab,
  type Theme,
} from '../src/theme/tokens';
import { contrastRatio } from './contrast';

const read = (rel: string) =>
  readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8').replace(
    /\/\*[\s\S]*?\*\//g,
    '',
  );
const themeCss = read('../src/theme/theme.css');
const appCss = read('../src/App.css');

function tokensFor(selectorPattern: RegExp): Record<string, string> {
  const rule = [...themeCss.matchAll(/([^{}]+)\{([^}]*)\}/g)].find((m) =>
    selectorPattern.test(m[1]?.trim() ?? ''),
  );
  expect(rule).toBeDefined();
  const out: Record<string, string> = {};
  for (const [, name, value] of (rule?.[2] ?? '').matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) {
    out[name as string] = (value as string).trim().toLowerCase();
  }
  return out;
}

const selectors: Record<Theme, RegExp> = {
  light: /^:root,\s*:root\[data-theme='light'\]$/,
  dark: /^:root\[data-theme='dark'\]$/,
};

describe('theme.css mirrors tokens.ts (S-1.6.5 AC1/AC2)', () => {
  for (const theme of THEMES) {
    it(`defines exactly the ${theme} tokens with identical values`, () => {
      const css = tokensFor(selectors[theme]);
      const expected = Object.fromEntries(
        TOKEN_NAMES.map((n) => [`--${kebab(n)}`, TOKENS[theme][n].toLowerCase()]),
      );
      const { 'color-scheme': _ignored, ...tokenVars } = css;
      void _ignored;
      expect(tokenVars).toEqual(expected);
    });
  }

  it('declares color-scheme per theme', () => {
    expect(themeCss).toMatch(/color-scheme:\s*light/);
    expect(themeCss).toMatch(/color-scheme:\s*dark/);
  });

  it('App.css uses no hard-coded colours', () => {
    expect(appCss).not.toMatch(/#[0-9a-f]{3,8}\b/i);
    expect(appCss).not.toMatch(/\b(rgb|hsl)a?\(/i);
  });

  it('index.html sets data-theme before React with the same storage key', () => {
    const html = readFileSync(fileURLToPath(new URL('../index.html', import.meta.url)), 'utf8');
    expect(html).toContain("'mtg-stats-theme'");
    expect(html).toContain("setAttribute('data-theme'");
    expect(html.indexOf('data-theme')).toBeLessThan(html.indexOf('/src/main.tsx'));
  });
});

describe('text and ring contrast (S-1.6.5 AC2)', () => {
  for (const theme of THEMES) {
    for (const pair of CONTRAST_PAIRS) {
      it(`${theme}: ${pair.what} (${pair.fg} on ${pair.bg}) is at least ${String(pair.min)}:1`, () => {
        const ratio = contrastRatio(TOKENS[theme][pair.fg], TOKENS[theme][pair.bg]);
        expect(ratio).toBeGreaterThanOrEqual(pair.min);
      });
    }
  }

  it('computes known ratios correctly', () => {
    expect(contrastRatio('#000000', '#ffffff')).toBeCloseTo(21, 5);
    expect(contrastRatio('#777777', '#ffffff')).toBeCloseTo(4.48, 2);
  });
});

/** Returns the bodies of every @media block whose query matches. */
function mediaBodies(query: RegExp): string[] {
  const out: string[] = [];
  const re = /@media\s*([^{]+)\{/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(appCss))) {
    let depth = 1;
    let i = re.lastIndex;
    while (i < appCss.length && depth > 0) {
      if (appCss[i] === '{') depth++;
      else if (appCss[i] === '}') depth--;
      i++;
    }
    if (query.test(m[1] ?? '')) out.push(appCss.slice(re.lastIndex, i - 1));
  }
  return out;
}

describe('tap targets below 768px (S-1.6.5 AC3, static)', () => {
  const body = mediaBodies(/width\s*<\s*768px|max-width:\s*767(\.98)?px/).join('\n');
  const rule = [...body.matchAll(/([^{}]+)\{([^}]*)\}/g)].find((m) =>
    /min-height:\s*44px/.test(m[2] ?? ''),
  );

  it('sets min-width and min-height of 44px', () => {
    expect(rule?.[2]).toMatch(/min-width:\s*44px/);
    expect(rule?.[2]).toMatch(/min-height:\s*44px/);
  });

  it.each(['button', 'input', 'select', 'textarea', "[role='button']", '.shell-nav a'])(
    'covers %s',
    (sel) => {
      expect(rule?.[1]).toContain(sel);
    },
  );
});

describe('reduced motion (S-1.6.5 AC4, static)', () => {
  it('disables animations and transitions under prefers-reduced-motion: reduce', () => {
    const body = mediaBodies(/prefers-reduced-motion:\s*reduce/).join('\n');
    expect(body).toMatch(/animation:\s*none\s*!important/);
    expect(body).toMatch(/transition:\s*none\s*!important/);
    expect(body).toMatch(/scroll-behavior:\s*auto/);
  });
});
