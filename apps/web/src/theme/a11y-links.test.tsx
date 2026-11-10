// S-1.6.5 AC2: every link the app renders gets a contrast-checked colour. Nav links and the skip
// link have their own rules; any other <a> (for example the 404 "Go home" link) must be reached
// by the global `a, a:visited` rule, which uses the --link token.
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { render } from '@testing-library/react';
import { createMemoryRouter } from 'react-router';
import { RouterProvider } from 'react-router/dom';
import { describe, expect, it } from 'vitest';
import { routes } from '../routes';
import { CONTRAST_PAIRS, TOKENS, THEMES } from './tokens';
import { contrastRatio } from '../../tests/contrast';

const css = readFileSync(
  resolve(dirname(fileURLToPath(import.meta.url)), '../App.css'),
  'utf8',
).replace(/\/\*[\s\S]*?\*\//g, '');

/** Top-level (non-@media) rules that set `color`, as [selectors, value]. */
const colourRules = [
  ...css.replace(/@media[^{]+\{([^{}]*\{[^{}]*\})*[^{}]*\}/g, '').matchAll(/([^{}]+)\{([^}]*)\}/g),
]
  .map((m) => ({
    selectors: (m[1] ?? '').split(',').map((s) => s.trim()),
    color: /(?:^|[;\s])color:\s*([^;]+)/.exec(m[2] ?? '')?.[1]?.trim(),
  }))
  .filter((r) => r.color !== undefined);

const stripState = (sel: string) => sel.replace(/:(visited|hover|focus|focus-visible)\b/g, '');

function rulesFor(el: Element) {
  return colourRules.filter((r) =>
    r.selectors.some((s) => {
      try {
        return el.matches(stripState(s));
      } catch {
        return false;
      }
    }),
  );
}

describe('links are coloured by a contrast-checked rule (S-1.6.5 AC2)', () => {
  it('the global a rule uses --link and its pair is in CONTRAST_PAIRS at 4.5:1 in both themes', () => {
    const rule = colourRules.find((r) => r.selectors.join(',') === 'a,a:visited');
    expect(rule?.color).toBe('var(--link)');
    const pair = CONTRAST_PAIRS.find((p) => p.fg === 'link' && p.bg === 'bg');
    expect(pair?.min).toBe(4.5);
    for (const theme of THEMES) {
      expect(contrastRatio(TOKENS[theme].link, TOKENS[theme].bg)).toBeGreaterThanOrEqual(4.5);
    }
  });

  it.each([
    '/',
    '/games',
    '/decks',
    '/stats',
    '/collection',
    '/leagues',
    '/about',
    '/no-such-page',
    '/sign-in',
  ])('every a[href] on %s matches a colour rule, and plain links resolve to --link', (path) => {
    const { container } = render(
      <RouterProvider router={createMemoryRouter(routes, { initialEntries: [path] })} />,
    );
    for (const a of container.querySelectorAll('a[href]')) {
      const matched = rulesFor(a);
      expect(matched.length, a.outerHTML).toBeGreaterThan(0);
      const special = a.matches('.shell-nav a, .skip-link');
      if (!special) expect(matched.at(-1)?.color, a.outerHTML).toBe('var(--link)');
    }
  });

  it('footer links sit on --muted-bg, and --link on --muted-bg meets 4.5:1 in both themes (S-1.6.6 AC4)', () => {
    const pair = CONTRAST_PAIRS.find((p) => p.fg === 'link' && p.bg === 'mutedBg');
    expect(pair?.min).toBe(4.5);
    for (const theme of THEMES) {
      expect(contrastRatio(TOKENS[theme].link, TOKENS[theme].mutedBg)).toBeGreaterThanOrEqual(4.5);
    }
    const { container } = render(
      <RouterProvider router={createMemoryRouter(routes, { initialEntries: ['/about'] })} />,
    );
    const links = container.querySelectorAll('footer a[href]');
    expect(links.length).toBe(3);
    for (const a of links) expect(rulesFor(a).at(-1)?.color).toBe('var(--link)');
  });

  it('the 404 "Go home" link is styled by the global link rule', () => {
    const { getByRole } = render(
      <RouterProvider router={createMemoryRouter(routes, { initialEntries: ['/no-such-page'] })} />,
    );
    const home = getByRole('link', { name: 'Go home' });
    expect(home.closest('.shell-nav')).toBeNull();
    expect(rulesFor(home).map((r) => r.color)).toEqual(['var(--link)']);
  });
});
