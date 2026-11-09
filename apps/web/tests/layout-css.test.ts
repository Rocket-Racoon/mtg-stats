// @vitest-environment node
// Static CSS checks. jsdom cannot lay out or paint, so these read src/App.css directly.
// Real rendering (visible ring, 360px no horizontal scroll, sidebar at 1024px) is a Playwright
// concern (S-1.8.5) and MANUAL until then.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

interface Rule {
  selector: string;
  decls: Map<string, string>;
}
interface Block {
  media: string | null;
  rules: Rule[];
}

const css = readFileSync(fileURLToPath(new URL('../src/App.css', import.meta.url)), 'utf8').replace(
  /\/\*[\s\S]*?\*\//g,
  '',
);

function parseDecls(body: string): Map<string, string> {
  const m = new Map<string, string>();
  for (const part of body.split(';')) {
    const i = part.indexOf(':');
    if (i > 0) m.set(part.slice(0, i).trim().toLowerCase(), part.slice(i + 1).trim());
  }
  return m;
}

function parse(text: string, media: string | null, out: Block[]): void {
  const block: Block = { media, rules: [] };
  out.push(block);
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
    if (head.startsWith('@media')) parse(body, head.slice(6).trim(), out);
    else block.rules.push({ selector: head, decls: parseDecls(body) });
    i = j;
  }
}

const blocks: Block[] = [];
parse(css, null, blocks);
const allRules = blocks.flatMap((b) => b.rules.map((r) => ({ ...r, media: b.media })));
const selectors = (r: { selector: string }) => r.selector.split(',').map((s) => s.trim());

// Media queries are matched in both the range form `(width >= 768px)` and the legacy
// `(min-width: 768px)` form, so harmless rewrites do not break these checks.
const num = '(\\d+(?:\\.\\d+)?)';
function minBound(media: string): number | null {
  const m =
    new RegExp(`\\(\\s*min-width:\\s*${num}px\\s*\\)`).exec(media) ??
    new RegExp(`\\(\\s*width\\s*>=\\s*${num}px\\s*\\)`).exec(media);
  return m ? Number(m[1]) : null;
}
/** Exclusive upper bound: `max-width: 767px` and `max-width: 767.98px` both give 768. */
function maxExclusive(media: string): number | null {
  const legacy = new RegExp(`\\(\\s*max-width:\\s*${num}px\\s*\\)`).exec(media);
  if (legacy) return Math.floor(Number(legacy[1])) + 1;
  const range = new RegExp(`\\(\\s*width\\s*<\\s*${num}px\\s*\\)`).exec(media);
  return range ? Number(range[1]) : null;
}
const atLeast = (n: number) => allRules.filter((r) => r.media !== null && minBound(r.media) === n);
const below = (n: number) =>
  allRules.filter((r) => r.media !== null && maxExclusive(r.media) === n);

const toPx = (v: string): number | null => {
  const m = /^(\d+(?:\.\d+)?)(px|rem|em)?$/.exec(v);
  if (!m) return null;
  return Number(m[1]) * (m[2] === 'rem' || m[2] === 'em' ? 16 : 1);
};

// These are static smoke checks on the stylesheet text. They do not prove AC2: real layout at
// 360px, 768px and 1024px, and the painted focus ring, are Playwright (S-1.8.5) or manual checks.
describe('focus ring (S-1.6.2 AC3, static smoke)', () => {
  // Matches a bare `a` in the selector, including inside :where(...) / :is(...).
  const targetsLinks = (s: string) =>
    /:focus-visible/.test(s) && /(^|[\s(,>+~])a(?=[\s,):[.#]|$)/.test(s);
  const rules = allRules.filter((r) => selectors(r).some(targetsLinks));

  it('has a :focus-visible rule that applies to links', () => {
    expect(rules.length).toBeGreaterThan(0);
  });

  it('sets a visible outline of at least 2px', () => {
    const widths = rules.flatMap((r) => {
      const long = r.decls.get('outline-width');
      const short = (r.decls.get('outline') ?? '')
        .split(/\s+/)
        .find((t) => /^\d+(\.\d+)?(px|rem|em)$/.test(t));
      const v = long ?? short;
      const style = r.decls.get('outline-style');
      const shorthand = r.decls.get('outline') ?? '';
      if (style === 'none' || /\bnone\b/.test(shorthand)) return [];
      const px = v === undefined ? null : toPx(v);
      return px === null ? [] : [px];
    });
    expect(widths.length).toBeGreaterThan(0);
    expect(Math.max(...widths)).toBeGreaterThanOrEqual(2);
  });

  it('never removes the outline on any rule (none, 0, 0px, outline-style none, outline-width 0)', () => {
    const removes = (r: { decls: Map<string, string> }) => {
      const tokens = (r.decls.get('outline') ?? '').split(/\s+/).filter(Boolean);
      const shorthand = tokens.some((t) => t === 'none' || /^0(px|rem|em)?$/.test(t));
      const style = r.decls.get('outline-style') === 'none';
      const w = r.decls.get('outline-width');
      return shorthand || style || (w !== undefined && toPx(w) === 0);
    };
    expect(allRules.filter(removes).map((r) => r.selector)).toEqual([]);
  });
});

describe('responsive layout (S-1.6.2 AC2, static smoke)', () => {
  it('turns the layout into a sidebar at 1024px and up', () => {
    const shell = atLeast(1024).find((r) => r.selector === '.shell');
    expect(shell).toBeDefined();
    expect(shell?.decls.get('grid-template-areas') ?? '').toMatch(/nav main/);
    expect(shell?.decls.get('grid-template-columns')).toMatch(/^\S+\s+minmax\(0,\s*1fr\)$/);
  });

  it('hides the nav until opened below 768px', () => {
    const hide = below(768).find(
      (r) => r.selector.includes('.shell-nav') && r.decls.get('display') === 'none',
    );
    expect(hide).toBeDefined();
    expect(hide?.selector).toContain('data-open');
  });

  it('shows the menu toggle below 768px and hides it from 768px up', () => {
    const base = allRules.find((r) => r.media === null && r.selector === '.menu-toggle');
    expect(base?.decls.get('display')).not.toBe('none');
    const wide = atLeast(768).find((r) => r.selector === '.menu-toggle');
    expect(wide?.decls.get('display')).toBe('none');
  });

  it('sets no fixed width or min-width above 360px outside wide-viewport media queries', () => {
    const offenders = allRules.filter((r) => {
      if (r.media !== null) {
        const min = minBound(r.media);
        if (min !== null && min > 360) return false;
      }
      return ['width', 'min-width'].some((p) => {
        const n = toPx(r.decls.get(p) ?? '');
        return n !== null && n > 360;
      });
    });
    expect(offenders).toEqual([]);
  });
});
