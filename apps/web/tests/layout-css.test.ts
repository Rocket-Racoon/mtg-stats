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
const inMedia = (re: RegExp) => allRules.filter((r) => r.media !== null && re.test(r.media));
const selectors = (r: { selector: string }) => r.selector.split(',').map((s) => s.trim());

describe('focus ring (S-1.6.2 AC3, static)', () => {
  const rules = allRules.filter((r) => selectors(r).some((s) => /(^|\s)a:focus-visible$/.test(s)));

  it('has a :focus-visible rule that applies to nav links', () => {
    expect(rules.length).toBeGreaterThan(0);
  });

  it('sets a visible outline of at least 2px', () => {
    const outline = rules.map((r) => r.decls.get('outline') ?? '').find((v) => v !== '');
    expect(outline).toBeDefined();
    expect(outline).not.toMatch(/\bnone\b/);
    const width = /(\d+(?:\.\d+)?)px/.exec(outline as string);
    expect(width).not.toBeNull();
    expect(Number((width as RegExpExecArray)[1])).toBeGreaterThanOrEqual(2);
  });

  it('never removes the outline (outline: none/0) on any rule', () => {
    const offenders = allRules.filter((r) => /^(none|0)\b/.test(r.decls.get('outline') ?? ''));
    expect(offenders).toEqual([]);
  });
});

describe('responsive layout (S-1.6.2 AC2, static)', () => {
  it('turns the layout into a sidebar at min-width 1024px', () => {
    const rules = inMedia(/\(min-width:\s*1024px\)/);
    const shell = rules.find((r) => r.selector === '.shell');
    expect(shell).toBeDefined();
    const areas = shell?.decls.get('grid-template-areas') ?? '';
    expect(areas).toMatch(/nav main/);
    expect(shell?.decls.get('grid-template-columns')).toMatch(/^\S+\s+minmax\(0,\s*1fr\)$/);
  });

  it('hides the nav until opened below 768px', () => {
    const rules = inMedia(/\(max-width:\s*767(\.98)?px\)/);
    const hide = rules.find(
      (r) => r.selector.includes('.shell-nav') && r.decls.get('display') === 'none',
    );
    expect(hide).toBeDefined();
    expect(hide?.selector).toContain('data-open');
  });

  it('shows the menu toggle below 768px and hides it from 768px up', () => {
    const base = allRules.find((r) => r.media === null && r.selector === '.menu-toggle');
    expect(base?.decls.get('display')).not.toBe('none');
    const wide = inMedia(/\(min-width:\s*768px\)/).find((r) => r.selector === '.menu-toggle');
    expect(wide?.decls.get('display')).toBe('none');
  });

  it('sets no fixed width or min-width above 360px (no forced horizontal scroll)', () => {
    const px = (v: string): number | null => {
      const m = /^(\d+(?:\.\d+)?)(px|rem|em)$/.exec(v);
      if (!m) return null;
      return Number(m[1]) * (m[2] === 'px' ? 1 : 16);
    };
    const offenders = allRules.filter((r) =>
      ['width', 'min-width'].some((p) => {
        const n = px(r.decls.get(p) ?? '');
        return n !== null && n > 360;
      }),
    );
    expect(offenders).toEqual([]);
  });
});
