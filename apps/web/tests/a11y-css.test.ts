// @vitest-environment node
// S-1.6.5 AC2 (contrast coverage) and AC4 (reduced motion coverage), static over the CSS.
// Limit: jsdom cannot paint, so the effective background of each selector is declared in
// CONTEXT below (from the DOM structure in App.tsx) instead of computed. A new selector that sets
// colour or background fails until it is added here AND its pair is in CONTRAST_PAIRS.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { CONTRAST_PAIRS, TOKEN_NAMES, kebab, type TokenName } from '../src/theme/tokens';

const read = (rel: string) =>
  readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8').replace(
    /\/\*[\s\S]*?\*\//g,
    '',
  );
const appCss = read('../src/App.css');
const themeCss = read('../src/theme/theme.css');

interface Rule {
  selector: string;
  decls: Map<string, string>;
  media: string | null;
}

function parse(css: string): Rule[] {
  const rules: Rule[] = [];
  const walk = (text: string, media: string | null) => {
    let i = 0;
    while (i < text.length) {
      const open = text.indexOf('{', i);
      if (open < 0) break;
      const head = text.slice(i, open).trim();
      let depth = 1;
      let j = open + 1;
      while (j < text.length && depth > 0) {
        if (text[j] === '{') depth++;
        else if (text[j] === '}') depth--;
        j++;
      }
      const body = text.slice(open + 1, j - 1);
      if (head.startsWith('@media')) walk(body, head.replace('@media', '').trim());
      else if (head.startsWith('@')) rules.push({ selector: head, decls: new Map(), media });
      else {
        const decls = new Map<string, string>();
        for (const part of body.split(';')) {
          const k = part.indexOf(':');
          if (k > 0) decls.set(part.slice(0, k).trim().toLowerCase(), part.slice(k + 1).trim());
        }
        rules.push({ selector: head.replace(/\s+/g, ' '), decls, media });
      }
      i = j;
    }
  };
  walk(css, null);
  return rules;
}

const appRules = parse(appCss);
const allRules = [...appRules, ...parse(themeCss)];

const tokenOf = (value: string | undefined): TokenName | undefined => {
  const m = /var\(--([\w-]+)\)/.exec(value ?? '');
  return TOKEN_NAMES.find((n) => kebab(n) === m?.[1]);
};
const hasPair = (fg: TokenName, bg: TokenName) =>
  CONTRAST_PAIRS.some((p) => p.fg === fg && p.bg === bg);

interface Context {
  color?: TokenName;
  background?: TokenName;
  pair: [TokenName, TokenName];
}

/** Every selector in App.css that sets a text or background colour, with its effective pair. */
const CONTEXT: Record<string, Context> = {
  ':root': { color: 'fg', background: 'bg', pair: ['fg', 'bg'] },
  '.shell-nav': { background: 'mutedBg', pair: ['fg', 'mutedBg'] },
  '.shell-nav a': { color: 'fg', pair: ['fg', 'mutedBg'] },
  '.shell-nav a:hover': { background: 'hoverBg', pair: ['fg', 'hoverBg'] },
  ".shell-nav a[aria-current='page']": {
    color: 'onAccent',
    background: 'accent',
    pair: ['onAccent', 'accent'],
  },
  '.menu-toggle, .theme-toggle': { color: 'fg', background: 'bg', pair: ['fg', 'bg'] },
  ".theme-toggle[aria-pressed='true']": { background: 'mutedBg', pair: ['fg', 'mutedBg'] },
  '.shell-footer': { color: 'mutedFg', background: 'mutedBg', pair: ['mutedFg', 'mutedBg'] },
  'a, a:visited': { color: 'link', pair: ['link', 'bg'] },
  '.open-in-app, .open-in-app:visited': {
    color: 'onAccent',
    background: 'accent',
    pair: ['onAccent', 'accent'],
  },
  '.account-menu button, .dialog button': { color: 'fg', background: 'bg', pair: ['fg', 'bg'] },
  '.account-menu-list, .dialog': { color: 'fg', background: 'bg', pair: ['fg', 'bg'] },
  '.toast': { color: 'fg', background: 'mutedBg', pair: ['fg', 'mutedBg'] },
  '.skip-link, .skip-link:visited': { color: 'fg', background: 'bg', pair: ['fg', 'bg'] },
};

describe('every text/background combination in App.css is contrast-checked (S-1.6.5 AC2)', () => {
  const colouring = appRules.filter(
    (r) => r.decls.has('color') || r.decls.has('background') || r.decls.has('background-color'),
  );

  it('has a declared context for every selector that sets colour or background', () => {
    expect(colouring.map((r) => r.selector).sort()).toEqual(Object.keys(CONTEXT).sort());
  });

  for (const rule of colouring) {
    const ctx = CONTEXT[rule.selector];
    it(`${rule.selector}: declared tokens match and the pair is in CONTRAST_PAIRS`, () => {
      expect(ctx).toBeDefined();
      if (!ctx) return;
      expect(tokenOf(rule.decls.get('color'))).toBe(ctx.color);
      expect(tokenOf(rule.decls.get('background') ?? rule.decls.get('background-color'))).toBe(
        ctx.background,
      );
      expect(hasPair(...ctx.pair)).toBe(true);
      const min = CONTRAST_PAIRS.find((p) => p.fg === ctx.pair[0] && p.bg === ctx.pair[1])?.min;
      expect(min).toBeGreaterThanOrEqual(4.5);
    });
  }

  it('uses only tokens, never literal colours, for text and background', () => {
    for (const r of colouring) {
      for (const k of ['color', 'background', 'background-color']) {
        const v = r.decls.get(k);
        if (v !== undefined) expect(v).toMatch(/^var\(--[\w-]+\)$/);
      }
    }
  });

  it('covers focus rings: default ring on page and nav, strong ring on the active link', () => {
    const ring = appRules.find((r) => r.selector === 'a:focus-visible, button:focus-visible');
    expect(tokenOf(ring?.decls.get('outline'))).toBe('focus');
    expect(hasPair('focus', 'bg')).toBe(true);
    expect(hasPair('focus', 'mutedBg')).toBe(true);
    const strong = appRules.find(
      (r) => r.selector === ".shell-nav a[aria-current='page']:focus-visible",
    );
    expect(tokenOf(strong?.decls.get('outline-color'))).toBe('focusStrong');
    expect(hasPair('focusStrong', 'accent')).toBe(true);
    expect(hasPair('focusStrong', 'mutedBg')).toBe(true);
  });

  it('every var(--token) used in App.css is defined in both themes', () => {
    const used = new Set([...appCss.matchAll(/var\(--([\w-]+)\)/g)].map((m) => m[1]));
    const defined = parse(themeCss).filter((r) => r.selector.startsWith(':root'));
    expect(defined.length).toBe(2);
    for (const name of used) {
      for (const r of defined) expect(r.decls.has(`--${String(name)}`)).toBe(true);
    }
  });
});

describe('reduced motion covers every motion declaration (S-1.6.5 AC4)', () => {
  const REDUCED = /prefers-reduced-motion:\s*reduce/;
  const motionProp = /^(transition|animation|scroll-behavior)(-|$)/;
  const reduced = allRules.filter((r) => r.media && REDUCED.test(r.media));
  const outside = allRules.filter((r) => !r.media || !REDUCED.test(r.media));

  it('has a reduced-motion block', () => {
    expect(reduced.length).toBeGreaterThan(0);
  });

  it('the block targets every element including pseudo-elements', () => {
    const selectors = reduced.flatMap((r) => r.selector.split(',').map((s) => s.trim()));
    expect(selectors).toEqual(expect.arrayContaining(['*', '*::before', '*::after']));
  });

  it('disables both transition and animation with !important so no selector can out-rank it', () => {
    const decls = reduced.find((r) => r.selector.includes('*'))?.decls;
    expect(decls?.get('transition')).toMatch(/^none\s*!important$/);
    expect(decls?.get('animation')).toMatch(/^none\s*!important$/);
  });

  it('there is at least one existing transition to cover (guards the test itself)', () => {
    expect(outside.some((r) => r.decls.has('transition'))).toBe(true);
  });

  it('every existing transition/animation/scroll-behavior declaration is neutralised', () => {
    const neutralised = new Set(
      reduced.flatMap((r) => [...r.decls].filter(([, v]) => /none|auto/.test(v)).map(([k]) => k)),
    );
    for (const r of outside) {
      for (const prop of r.decls.keys()) {
        if (!motionProp.test(prop)) continue;
        // Longhands such as transition-duration are covered by their shorthand reset.
        const shorthand = prop.replace(/-.*/, '');
        expect(neutralised.has(shorthand), `${r.selector} { ${prop} }`).toBe(true);
      }
    }
  });

  it('keyframes, if any exist, are only reachable through animation, which the block resets', () => {
    const keyframes = allRules.filter((r) => r.selector.startsWith('@keyframes'));
    if (keyframes.length > 0) {
      expect(reduced.some((r) => r.decls.get('animation')?.startsWith('none'))).toBe(true);
    }
  });

  it('no JS-driven smooth scroll or animation bypasses the CSS rule', () => {
    const srcDir = fileURLToPath(new URL('../src/', import.meta.url));
    for (const f of ['App.tsx', 'main.tsx', 'theme/ThemeToggle.tsx', 'theme/store.ts']) {
      const text = readFileSync(srcDir + f, 'utf8');
      expect(text, f).not.toMatch(/behavior:\s*['"]smooth['"]|\.animate\(|requestAnimationFrame/);
    }
  });
});
