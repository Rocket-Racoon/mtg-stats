// S-1.6.5 AC3. jsdom cannot lay out, so this checks that the < 768px 44x44 rule in App.css
// selects every interactive element the app really renders. Measured sizes in a real browser
// belong to S-1.8.5.
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { render } from '@testing-library/react';
import { createMemoryRouter } from 'react-router';
import { RouterProvider } from 'react-router/dom';
import { describe, expect, it } from 'vitest';
import { routes } from '../routes';

const css = readFileSync(
  resolve(dirname(fileURLToPath(import.meta.url)), '../App.css'),
  'utf8',
).replace(/\/\*[\s\S]*?\*\//g, '');

/** Top-level @media blocks as [query, body]. */
function mediaBlocks(): [string, string][] {
  const out: [string, string][] = [];
  const re = /@media\s*([^{]+)\{/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(css))) {
    let depth = 1;
    let i = re.lastIndex;
    while (i < css.length && depth > 0) {
      if (css[i] === '{') depth++;
      else if (css[i] === '}') depth--;
      i++;
    }
    out.push([(m[1] ?? '').trim(), css.slice(re.lastIndex, i - 1)]);
  }
  return out;
}

const SMALL = /width\s*<\s*768px|max-width:\s*767(\.98)?px/;
const small = mediaBlocks().filter(([q]) => SMALL.test(q));
const tapRule = small
  .flatMap(([, body]) => [...body.matchAll(/([^{}]+)\{([^}]*)\}/g)])
  .find((m) => /min-height:\s*44px/.test(m[2] ?? ''));
const selectorList = (tapRule?.[1] ?? '')
  .split(',')
  .map((s) => s.trim())
  .filter(Boolean);

function renderApp() {
  return render(<RouterProvider router={createMemoryRouter(routes, { initialEntries: ['/'] })} />);
}

const INTERACTIVE =
  "a[href], button, input:not([type='hidden']), select, textarea, summary, [role='button'], [tabindex]:not([tabindex='-1'])";

describe('44px tap targets below 768px (S-1.6.5 AC3)', () => {
  it('declares min-width and min-height of 44px inside a < 768px media query', () => {
    expect(tapRule).toBeDefined();
    expect(tapRule?.[2]).toMatch(/min-width:\s*44px/);
    expect(tapRule?.[2]).toMatch(/min-height:\s*44px/);
  });

  it('does not apply the rule only at 768px and up, or outside any query', () => {
    const wide = mediaBlocks().filter(([q]) => /width\s*>=\s*\d+px|min-width/.test(q));
    for (const [, body] of wide) expect(body).not.toMatch(/min-height:\s*44px/);
  });

  it('matches every interactive element rendered today', () => {
    const { container } = renderApp();
    const elements = [...container.querySelectorAll<HTMLElement>(INTERACTIVE)];
    // skip link, menu button, theme toggle and the nav links at minimum
    expect(elements.length).toBeGreaterThanOrEqual(4);
    const unmatched = elements.filter((el) => !selectorList.some((sel) => el.matches(sel)));
    expect(unmatched.map((el) => el.outerHTML)).toEqual([]);
  });

  it.each([
    ['skip link', '.skip-link'],
    ['menu button', '.menu-toggle'],
    ['theme toggle', '.theme-toggle'],
    ['nav links', '.shell-nav a'],
  ])('covers the %s', (_name, selector) => {
    const { container } = renderApp();
    const els = [...container.querySelectorAll<HTMLElement>(selector)];
    expect(els.length).toBeGreaterThan(0);
    for (const el of els) expect(selectorList.some((sel) => el.matches(sel))).toBe(true);
  });

  it('nothing caps an element below 44px with height or width declarations', () => {
    expect(css).not.toMatch(/(^|[;{\s])(max-height|max-width|height|width)\s*:\s*\d/);
  });
});
