// @vitest-environment node
// S-1.6.6 AC4, static over the CSS. jsdom cannot lay out, so this only asserts that nothing in the
// footer's CSS prevents wrapping. The real 360px "no horizontal scroll" measurement is the
// real-browser check of S-1.8.5 (Playwright) and MANUAL until then.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { CONTRAST_PAIRS } from '../src/theme/tokens';

const css = readFileSync(fileURLToPath(new URL('../src/App.css', import.meta.url)), 'utf8').replace(
  /\/\*[\s\S]*?\*\//g,
  '',
);
const rules = [...css.matchAll(/([^{}@]+)\{([^}]*)\}/g)].map((m) => ({
  selector: (m[1] ?? '').trim(),
  body: m[2] ?? '',
}));
const footerRules = rules.filter(({ selector }) =>
  selector
    .split(',')
    .some((s) => /(^|\s)\.(shell-footer|attribution|footer-controls|footer-link)\b/.test(s.trim())),
);
const bodyOf = (selector: string): string => {
  const rule = footerRules.find((r) => r.selector === selector);
  if (!rule) throw new Error(`no rule for ${selector}`);
  return rule.body;
};

describe('footer CSS cannot force overflow at 360px (S-1.6.6 AC4)', () => {
  it('finds the footer rules (guards the parser)', () => {
    expect(footerRules.length).toBeGreaterThanOrEqual(4);
  });

  it('no footer rule uses nowrap or pre', () => {
    for (const { selector, body } of footerRules) {
      expect(body, selector).not.toMatch(/white-space:\s*(nowrap|pre)\b/);
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

  it('the footer wraps its children and the notice breaks long words and URLs', () => {
    expect(bodyOf('.shell-footer')).toMatch(/flex-wrap:\s*wrap/);
    expect(bodyOf('.attribution')).toMatch(/overflow-wrap:\s*(anywhere|break-word)/);
    expect(bodyOf('.attribution')).toMatch(/min-width:\s*0/);
  });

  it('footer colours come only from tokens, and the footer pair is in CONTRAST_PAIRS', () => {
    for (const { selector, body } of footerRules) {
      for (const m of body.matchAll(/(?:^|[;\s])(color|background(?:-color)?)\s*:\s*([^;]+)/g)) {
        expect(m[2]?.trim(), `${selector} ${m[1]}`).toMatch(/^var\(--[a-z-]+\)$/);
      }
    }
    expect(bodyOf('.shell-footer')).toMatch(/color:\s*var\(--muted-fg\)/);
    expect(bodyOf('.shell-footer')).toMatch(/background:\s*var\(--muted-bg\)/);
    expect(CONTRAST_PAIRS.some((p) => p.fg === 'mutedFg' && p.bg === 'mutedBg')).toBe(true);
  });
});
