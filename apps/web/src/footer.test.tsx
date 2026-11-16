// S-1.6.6: the footer notice is on every route, and /about shows the full notice and the version.
// Two layers on purpose: the first checks against the card-utils constants; the "AC wording" block
// hard-codes the acceptance-criteria text (independent of the module), so a wording change in the
// module fails it.
import { render, screen, within } from '@testing-library/react';
import {
  FAN_CONTENT_DISCLAIMER_TEXT,
  FAN_CONTENT_POLICY_URL,
  SCRYFALL_CREDIT_TEXT,
  SCRYFALL_URL,
} from '@mtg-stats/card-utils';
import { createMemoryRouter, type RouteObject } from 'react-router';
import { RouterProvider } from 'react-router/dom';
import { AppProviders } from './providers/AppProviders';
import { beforeEach, describe, expect, it } from 'vitest';
import { env } from './env';
import { routes } from './routes';
import { useSessionStore } from './session/store';

beforeEach(() => {
  useSessionStore.setState({ status: 'signed_in' }); // fake signed-in session: the guard lets pages render (S-1.6.4)
});

function renderAt(path: string) {
  return render(
    <AppProviders>
      <RouterProvider router={createMemoryRouter(routes, { initialEntries: [path] })} />
    </AppProviders>,
  );
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

const AC_DISCLAIMER =
  'MTG Stats is unofficial Fan Content permitted under the Fan Content Policy. Not approved/endorsed by Wizards. Portions of the materials used are property of Wizards of the Coast. ©Wizards of the Coast LLC.';
const AC_CREDIT = 'Card data and images courtesy of Scryfall';
const POLICY_HREF = 'https://company.wizards.com/en/legal/fancontentpolicy';

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

describe('footer wording is exactly the AC text (AC1)', () => {
  it.each(PATHS)('%s: footer paragraphs equal the AC strings', (path) => {
    renderAt(path);
    const footer = screen.getByRole('contentinfo');
    const paragraphs = [...footer.querySelectorAll('.attribution p')].map((p) =>
      // The hidden "Opens in a new tab" span is a description, not notice text.
      normalise(
        [...p.childNodes]
          .filter((n) => !(n instanceof HTMLElement && n.hasAttribute('hidden')))
          .map((n) => n.textContent)
          .join(''),
      ),
    );
    expect(paragraphs).toEqual([AC_DISCLAIMER, AC_CREDIT]);
  });

  it.each(PATHS)('%s: exactly one contentinfo landmark', (path) => {
    renderAt(path);
    expect(screen.getAllByRole('contentinfo')).toHaveLength(1);
  });

  it.each(PATHS)('%s: policy and Scryfall links have the pinned hrefs', (path) => {
    renderAt(path);
    const footer = screen.getByRole('contentinfo');
    expect(within(footer).getByRole('link', { name: 'Fan Content Policy' })).toHaveAttribute(
      'href',
      POLICY_HREF,
    );
    expect(within(footer).getByRole('link', { name: 'Scryfall' })).toHaveAttribute(
      'href',
      'https://scryfall.com',
    );
  });
});

describe('/about, AC wording and version (AC2)', () => {
  it('renders without redirecting, with the version from package.json', async () => {
    const router = createMemoryRouter(routes, { initialEntries: ['/about'] });
    render(
      <AppProviders>
        <RouterProvider router={router} />
      </AppProviders>,
    );
    expect(router.state.location.pathname).toBe('/about');
    const pkg = (await import('../package.json')) as { version: string };
    expect(screen.getByTestId('app-version')).toHaveTextContent(pkg.version);
    expect(env.appVersion).toBe(pkg.version);
  });

  it('shows the AC disclaimer, credit and an images-from-Scryfall note in main', () => {
    renderAt('/about');
    const text = normalise(screen.getByRole('main').textContent);
    expect(text).toContain(AC_DISCLAIMER);
    expect(text).toContain(AC_CREDIT);
    expect(text).toMatch(/card images are loaded .*from Scryfall/i);
  });

  it('is linked from the footer on the app shell and the public shell', () => {
    for (const path of ['/games', '/sign-in']) {
      const { unmount } = renderAt(path);
      expect(
        within(screen.getByRole('contentinfo')).getByRole('link', { name: 'About' }),
      ).toHaveAttribute('href', '/about');
      unmount();
    }
  });
});

describe('"Opens in a new tab" description', () => {
  it('exists once per external link, is hidden from sight but used as the description', () => {
    renderAt('/games');
    const footer = screen.getByRole('contentinfo');
    const links = within(footer)
      .getAllByRole('link')
      .filter((a) => a.getAttribute('target') === '_blank');
    expect(links).toHaveLength(2);
    const hints = [...footer.querySelectorAll('span')].filter(
      (s) => s.textContent === 'Opens in a new tab',
    );
    expect(hints).toHaveLength(2);
    for (const h of hints) expect(h).toHaveAttribute('hidden');
    for (const a of links) {
      const id = a.getAttribute('aria-describedby');
      expect(id).toBeTruthy();
      expect(a).toHaveAccessibleDescription('Opens in a new tab');
    }
  });

  it('ids stay unique when /about shows the notice twice (page body plus footer)', () => {
    const { container } = renderAt('/about');
    const ids = [...container.querySelectorAll('[id]')].map((e) => e.id);
    expect(new Set(ids).size).toBe(ids.length);
    const describedBy = [...container.querySelectorAll('a[aria-describedby]')].map((a) =>
      a.getAttribute('aria-describedby'),
    );
    expect(describedBy).toHaveLength(4);
    expect(new Set(describedBy).size).toBe(describedBy.length);
    for (const a of container.querySelectorAll('a[target="_blank"]')) {
      expect(a).toHaveAccessibleDescription('Opens in a new tab');
    }
  });
});
