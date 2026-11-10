// S-1.6.6 acceptance gaps: the AC wording is hard-coded here on purpose (independent of the
// card-utils module), so a wording change in the module fails this test.
import { render, screen, within } from '@testing-library/react';
import { createMemoryRouter } from 'react-router';
import { RouterProvider } from 'react-router/dom';
import { describe, expect, it } from 'vitest';
import { env } from './env';
import { routes } from './routes';

const AC_DISCLAIMER =
  'MTG Stats is unofficial Fan Content permitted under the Fan Content Policy. Not approved/endorsed by Wizards. Portions of the materials used are property of Wizards of the Coast. ©Wizards of the Coast LLC.';
const AC_CREDIT = 'Card data and images courtesy of Scryfall';
const POLICY_HREF = 'https://company.wizards.com/en/legal/fancontentpolicy';

function renderAt(path: string) {
  return render(<RouterProvider router={createMemoryRouter(routes, { initialEntries: [path] })} />);
}
const norm = (s: string | null) => (s ?? '').replace(/\s+/g, ' ').trim();

// App shell (/, /games, /about, 404) and public shell (/sign-in).
const PATHS = ['/', '/games', '/decks', '/stats', '/about', '/sign-in', '/no-such-page'];

describe('footer wording is exactly the AC text (AC1)', () => {
  it.each(PATHS)('%s: footer paragraphs equal the AC strings', (path) => {
    renderAt(path);
    const footer = screen.getByRole('contentinfo');
    const paragraphs = [...footer.querySelectorAll('.attribution p')].map((p) =>
      // The hidden "Opens in a new tab" span is a description, not notice text.
      norm(
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

describe('/about (AC2)', () => {
  it('renders without redirecting, with the version from package.json', async () => {
    const router = createMemoryRouter(routes, { initialEntries: ['/about'] });
    render(<RouterProvider router={router} />);
    expect(router.state.location.pathname).toBe('/about');
    const pkg = (await import('../package.json')) as { version: string };
    expect(screen.getByTestId('app-version')).toHaveTextContent(pkg.version);
    expect(env.appVersion).toBe(pkg.version);
  });

  it('shows the AC disclaimer, credit and an images-from-Scryfall note in main', () => {
    renderAt('/about');
    const text = norm(screen.getByRole('main').textContent);
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
