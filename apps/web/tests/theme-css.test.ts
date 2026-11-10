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
