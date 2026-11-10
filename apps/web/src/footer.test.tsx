// S-1.6.6: the footer notice is on every route, and /about shows the full notice and the version.
import { render, screen, within } from '@testing-library/react';
import {
  FAN_CONTENT_DISCLAIMER_TEXT,
  FAN_CONTENT_POLICY_URL,
  SCRYFALL_CREDIT_TEXT,
  SCRYFALL_URL,
} from '@mtg-stats/card-utils';
import { createMemoryRouter, type RouteObject } from 'react-router';
import { RouterProvider } from 'react-router/dom';
import { describe, expect, it } from 'vitest';
import { env } from './env';
import { routes } from './routes';

function renderAt(path: string) {
  return render(<RouterProvider router={createMemoryRouter(routes, { initialEntries: [path] })} />);
}

/** Every concrete path in the route table, plus an unknown one for the catch-all. */
function pathsOf(table: RouteObject[], parent = ''): string[] {
  return table.flatMap((r) => {
    const own =
      r.path === undefined
        ? parent
        : r.path.startsWith('/')
          ? r.path
          : `${parent.replace(/\/$/, '')}/${r.path}`;
    const isLeaf = !r.children && r.path !== '*';
    return [...(isLeaf ? [own || '/'] : []), ...(r.children ? pathsOf(r.children, own) : [])];
  });
}
const PATHS = [...new Set([...pathsOf(routes), '/', '/no-such-page'])];

const normalise = (s: string | null) => (s ?? '').replace(/\s+/g, ' ').trim();

describe('footer notice on every route (S-1.6.6 AC1)', () => {
  it('enumerates the route table (guards the helper)', () => {
    expect(PATHS).toEqual(
      expect.arrayContaining(['/', '/games', '/decks', '/stats', '/about', '/sign-in']),
    );
  });

  it.each(PATHS)('%s shows the disclaimer, the Scryfall credit and both links', (path) => {
    renderAt(path);
    const footer = screen.getByRole('contentinfo');
    const text = normalise(footer.textContent);
    expect(text).toContain(FAN_CONTENT_DISCLAIMER_TEXT);
    expect(text).toContain(SCRYFALL_CREDIT_TEXT);
    const policy = within(footer).getByRole('link', { name: 'Fan Content Policy' });
    expect(policy).toHaveAttribute('href', FAN_CONTENT_POLICY_URL);
    const scryfall = within(footer).getByRole('link', { name: 'Scryfall' });
    expect(scryfall).toHaveAttribute('href', SCRYFALL_URL);
  });

  it('external links use rel noopener noreferrer, open a new tab and are announced as such', () => {
    renderAt('/');
    for (const name of ['Fan Content Policy', 'Scryfall']) {
      const a = screen.getByRole('link', { name });
      expect(a).toHaveAttribute('rel', 'noopener noreferrer');
      expect(a).toHaveAttribute('target', '_blank');
      expect(a).toHaveAccessibleDescription('Opens in a new tab');
    }
  });

  it('is in the same footer as the theme toggle and an About link to /about', () => {
    renderAt('/games');
    const footer = screen.getByRole('contentinfo');
    expect(within(footer).getByRole('button', { name: 'Dark mode' })).toBeInTheDocument();
    expect(within(footer).getByRole('link', { name: 'About' })).toHaveAttribute('href', '/about');
  });
});

describe('/about page (S-1.6.6 AC2)', () => {
  it('shows the version, full disclaimer, Scryfall credit and the image-source note, no sign-in', () => {
    renderAt('/about');
    const main = screen.getByRole('main');
    expect(within(main).getByRole('heading', { level: 2, name: 'About' })).toBeInTheDocument();
    const text = normalise(main.textContent);
    expect(text).toContain(FAN_CONTENT_DISCLAIMER_TEXT);
    expect(text).toContain(SCRYFALL_CREDIT_TEXT);
    expect(text).toMatch(/Card images are loaded directly from Scryfall/);
    expect(within(main).getByTestId('app-version')).toHaveTextContent(env.appVersion);
    expect(within(main).getByRole('link', { name: 'Scryfall' })).toHaveAttribute(
      'href',
      SCRYFALL_URL,
    );
  });
});
