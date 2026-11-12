import { StrictMode } from 'react';
import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter } from 'react-router';
import { RouterProvider } from 'react-router/dom';
import { beforeEach, describe, expect, it } from 'vitest';
import { routes } from './routes';
import { useSessionStore } from './session/store';

beforeEach(() => {
  useSessionStore.setState({ status: 'signed_in' }); // fake signed-in session: the guard lets pages render (S-1.6.4)
});

function renderAt(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(
    <StrictMode>
      <RouterProvider router={router} />
    </StrictMode>,
  );
  return router;
}

const NAV = ['Home', 'Games', 'Decks', 'Stats', 'Collection', 'Leagues'] as const;

describe('routes exist (S-1.6.2 AC1)', () => {
  it.each([
    ['/', 'Home'],
    ['/games', 'Games'],
    ['/decks', 'Decks'],
    ['/stats', 'Stats'],
    ['/collection', 'Collection'],
    ['/leagues', 'Leagues'],
  ])('renders the %s page with heading "%s" inside the layout', (path, heading) => {
    renderAt(path);
    expect(
      within(screen.getByRole('main')).getByRole('heading', { level: 2, name: heading }),
    ).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: 'Primary' })).toBeInTheDocument();
  });

  it('renders /sign-in without the app navigation or header, but with the footer (S-1.6.6)', () => {
    renderAt('/sign-in');
    expect(screen.getByRole('heading', { level: 2, name: 'Sign in' })).toBeInTheDocument();
    expect(screen.queryByRole('navigation')).not.toBeInTheDocument();
    expect(screen.queryByRole('banner')).not.toBeInTheDocument();
    expect(screen.getByRole('main')).toBeInTheDocument();
    expect(screen.getByRole('contentinfo')).toBeInTheDocument();
  });

  it('shows the 404 page with a Go home link for an unknown deep path', () => {
    renderAt('/nope/deeper');
    expect(screen.getByRole('heading', { level: 2, name: 'Page not found' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Go home' })).toHaveAttribute('href', '/');
  });

  it('lands on Home when the 404 Go home link is clicked', async () => {
    const user = userEvent.setup();
    const router = renderAt('/nope/deeper');
    await user.click(screen.getByRole('link', { name: 'Go home' }));
    expect(router.state.location.pathname).toBe('/');
    expect(screen.getByRole('heading', { level: 2, name: 'Home' })).toBeInTheDocument();
  });

  it.each([
    ['/games', 'Games'],
    ['/decks', 'Decks'],
    ['/sign-in', 'Sign in'],
    ['/nope', 'Page not found'],
  ])('sets document.title for %s to mention "%s" and MTG Stats', (path, title) => {
    renderAt(path);
    expect(document.title).toContain(title);
    expect(document.title).toContain('MTG Stats');
  });
});

describe('skip link (S-1.6.2 AC3)', () => {
  it('is the first focusable element and targets #main', async () => {
    const user = userEvent.setup();
    renderAt('/');
    const skip = screen.getByRole('link', { name: 'Skip to main content' });
    expect(skip).toHaveAttribute('href', '#main');
    await user.tab();
    expect(skip).toHaveFocus();
  });

  it('targets a focusable main#main (the browser moves focus there on activation)', () => {
    // jsdom does not perform fragment navigation, so the focus move itself is a Playwright
    // (S-1.8.5) check; here we assert the target is valid and programmatically focusable.
    renderAt('/');
    const main = screen.getByRole('main');
    expect(main).toHaveAttribute('id', 'main');
    expect(main).toHaveAttribute('tabindex', '-1');
    main.focus();
    expect(main).toHaveFocus();
  });
});

describe('keyboard navigation (S-1.6.2 AC3)', () => {
  it('moves focus through the skip link, toggle and every nav link in order with Tab', async () => {
    const user = userEvent.setup();
    renderAt('/');
    const nav = screen.getByRole('navigation', { name: 'Primary' });
    await user.tab();
    expect(screen.getByRole('link', { name: 'Skip to main content' })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole('button', { name: 'Menu' })).toHaveFocus();
    for (const label of NAV) {
      await user.tab();
      expect(within(nav).getByRole('link', { name: label })).toHaveFocus();
    }
  });

  it('opens the focused link with Enter and moves aria-current', async () => {
    const user = userEvent.setup();
    renderAt('/');
    const nav = screen.getByRole('navigation', { name: 'Primary' });
    expect(within(nav).getByRole('link', { name: 'Home' })).toHaveAttribute('aria-current', 'page');
    await user.tab(); // skip link
    await user.tab(); // Menu toggle
    await user.tab(); // Home
    await user.tab(); // Games
    await user.tab(); // Decks
    expect(within(nav).getByRole('link', { name: 'Decks' })).toHaveFocus();
    await user.keyboard('{Enter}');
    expect(screen.getByRole('heading', { level: 2, name: 'Decks' })).toBeInTheDocument();
    expect(within(nav).getByRole('link', { name: 'Decks' })).toHaveAttribute(
      'aria-current',
      'page',
    );
    expect(within(nav).getByRole('link', { name: 'Home' })).not.toHaveAttribute('aria-current');
  });
});

describe('focus after navigation (S-1.6.2)', () => {
  it('does not move focus to main on initial render', () => {
    renderAt('/');
    expect(screen.getByRole('main')).not.toHaveFocus();
  });

  it('moves focus to main#main after clicking a nav link', async () => {
    const user = userEvent.setup();
    renderAt('/');
    await user.click(screen.getByRole('link', { name: 'Games' }));
    expect(document.activeElement).toBe(screen.getByRole('main'));
    expect(document.activeElement?.id).toBe('main');
  });

  it('does not move focus when only the search params change, but does on a pathname change', async () => {
    const router = renderAt('/games');
    const toggle = screen.getByRole('button', { name: 'Menu' });
    toggle.focus();
    await act(() => router.navigate('/games?x=1'));
    expect(toggle).toHaveFocus();
    await act(() => router.navigate('/decks'));
    expect(screen.getByRole('main')).toHaveFocus();
  });

  it('keeps focus on main across layouts: /sign-in -> footer About link -> /about', async () => {
    const user = userEvent.setup();
    const router = renderAt('/sign-in');
    expect(screen.getByRole('main')).not.toHaveFocus();
    await user.click(within(screen.getByRole('contentinfo')).getByRole('link', { name: 'About' }));
    expect(router.state.location.pathname).toBe('/about');
    expect(screen.getByRole('main')).toHaveFocus();
  });

  it('keeps focus on main across layouts: /about -> /sign-in', async () => {
    const router = renderAt('/about');
    await act(() => router.navigate('/sign-in'));
    expect(screen.getByRole('main')).toHaveFocus();
    expect(document.activeElement?.id).toBe('main');
  });
});

describe('compact menu toggle (S-1.6.2 AC2/AC3)', () => {
  it('is collapsed initially, labelled Menu, and controls #primary-nav', () => {
    renderAt('/');
    const toggle = screen.getByRole('button', { name: 'Menu' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(toggle).toHaveAttribute('aria-controls', 'primary-nav');
    expect(document.getElementById('primary-nav')).not.toBeNull();
  });

  it('opens with Enter, keeps focus on the toggle and its label, and Escape closes it', async () => {
    const user = userEvent.setup();
    renderAt('/');
    const toggle = screen.getByRole('button', { name: 'Menu' });
    await user.tab(); // skip link
    await user.tab();
    expect(toggle).toHaveFocus();
    await user.keyboard('{Enter}');
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(toggle).toHaveFocus();
    expect(screen.getByRole('button', { name: 'Menu' })).toBe(toggle);
    await user.keyboard('{Escape}');
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(toggle).toHaveFocus();
  });

  it('Escape from inside the nav closes the menu and returns focus to the toggle', async () => {
    const user = userEvent.setup();
    renderAt('/');
    const toggle = screen.getByRole('button', { name: 'Menu' });
    await user.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await user.tab(); // first nav link
    expect(screen.getByRole('link', { name: 'Home' })).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(toggle).toHaveFocus();
  });

  it('Escape pressed in main does not close the menu or move focus', async () => {
    const user = userEvent.setup();
    renderAt('/');
    const toggle = screen.getByRole('button', { name: 'Menu' });
    await user.click(toggle);
    const main = screen.getByRole('main');
    main.focus();
    await user.keyboard('{Escape}');
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(main).toHaveFocus();
  });

  it('closes when a link is clicked', async () => {
    const user = userEvent.setup();
    renderAt('/');
    const toggle = screen.getByRole('button', { name: 'Menu' });
    await user.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await user.click(screen.getByRole('link', { name: 'Stats' }));
    expect(screen.getByRole('heading', { level: 2, name: 'Stats' })).toBeInTheDocument();
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
  });

  it('closes when the current page link is clicked', async () => {
    const user = userEvent.setup();
    renderAt('/games');
    const toggle = screen.getByRole('button', { name: 'Menu' });
    await user.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await user.click(screen.getByRole('link', { name: 'Games' }));
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
  });

  it('closes when a link is opened with Enter', async () => {
    const user = userEvent.setup();
    renderAt('/');
    const toggle = screen.getByRole('button', { name: 'Menu' });
    await user.click(toggle);
    await user.tab(); // Home
    await user.tab(); // Games
    await user.keyboard('{Enter}');
    expect(screen.getByRole('heading', { level: 2, name: 'Games' })).toBeInTheDocument();
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
  });

  it('stays closed and does not move focus into the nav after history back (M1 regression)', async () => {
    const user = userEvent.setup();
    const router = renderAt('/games');
    const toggle = screen.getByRole('button', { name: 'Menu' });
    await user.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await user.click(screen.getByRole('link', { name: 'Decks' }));
    expect(router.state.location.pathname).toBe('/decks');
    await act(async () => {
      await router.navigate(-1);
    });
    expect(router.state.location.pathname).toBe('/games');
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    const nav = screen.getByRole('navigation', { name: 'Primary' });
    expect(nav.contains(document.activeElement)).toBe(false);
  });
});
