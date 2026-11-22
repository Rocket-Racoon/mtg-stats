// S-1.6.5 AC3. jsdom cannot lay out, so this checks that the < 768px 44x44 rule in App.css
// selects every interactive element the app really renders. Measured sizes in a real browser
// belong to S-1.8.5.
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { render } from '@testing-library/react';
import { createMemoryRouter } from 'react-router';
import { RouterProvider } from 'react-router/dom';
import { beforeEach, describe, expect, it } from 'vitest';
import { AppProviders } from '../providers/AppProviders';
import { routes } from '../routes';
import { useSessionStore } from '../session/store';

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

beforeEach(() => {
  useSessionStore.setState({ status: 'signed_in' }); // fake signed-in session: the guard lets pages render (S-1.6.4)
});

function renderApp() {
  return render(
    <AppProviders>
      <RouterProvider router={createMemoryRouter(routes, { initialEntries: ['/'] })} />
    </AppProviders>,
  );
}

const INTERACTIVE =
  "a[href], button, input:not([type='hidden']), select, textarea, summary, [role='button'], [tabindex]:not([tabindex='-1'])";

// The only exemption: links inside the notice paragraphs. Anything else that is not selected by
// the 44px rule fails the "matches every interactive element" test below.
const INLINE_LINK = '.attribution p a';

describe('44px tap targets below 768px (S-1.6.5 AC3)', () => {
  it('exempts only inline links in the notice sentences, and covers the standalone footer link', () => {
    const { container } = renderApp();
    const inline = [...container.querySelectorAll<HTMLElement>(INLINE_LINK)];
    expect(inline.map((a) => a.textContent)).toEqual(['Fan Content Policy', 'Scryfall']);
    for (const a of inline) expect(a.closest('p')?.textContent?.length).toBeGreaterThan(20);
    const about = container.querySelector<HTMLElement>('.footer-link');
    expect(about?.textContent).toBe('About');
    expect(selectorList.some((sel) => about?.matches(sel))).toBe(true);
  });

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
    // Inline links inside a sentence (the legal notices) are exempt: WCAG 2.5.8 "Inline" exception.
    const elements = [...container.querySelectorAll<HTMLElement>(INTERACTIVE)].filter(
      (el) => !el.matches(INLINE_LINK),
    );
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
