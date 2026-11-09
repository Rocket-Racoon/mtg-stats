import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter } from 'react-router';
import { RouterProvider } from 'react-router/dom';
import { describe, expect, it } from 'vitest';
import { routes } from './routes';

function renderAt(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(<RouterProvider router={router} />);
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

  it('renders /sign-in without the app navigation, header or footer', () => {
    renderAt('/sign-in');
    expect(screen.getByRole('heading', { level: 2, name: 'Sign in' })).toBeInTheDocument();
    expect(screen.queryByRole('navigation')).not.toBeInTheDocument();
    expect(screen.queryByRole('banner')).not.toBeInTheDocument();
    expect(screen.queryByRole('contentinfo')).not.toBeInTheDocument();
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

describe('keyboard navigation (S-1.6.2 AC3)', () => {
  it('moves focus through every nav link in order with Tab', async () => {
    const user = userEvent.setup();
    renderAt('/');
    const nav = screen.getByRole('navigation', { name: 'Primary' });
    // The Menu toggle comes first in the tab order (it is hidden by CSS on wide screens).
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

describe('compact menu toggle (S-1.6.2 AC2/AC3)', () => {
  it('is collapsed initially and controls #primary-nav', () => {
    renderAt('/');
    const toggle = screen.getByRole('button', { name: 'Menu' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(toggle).toHaveAttribute('aria-controls', 'primary-nav');
    expect(document.getElementById('primary-nav')).not.toBeNull();
  });

  it('opens with Enter, moves focus to the first link, and Escape closes and restores focus', async () => {
    const user = userEvent.setup();
    renderAt('/');
    const toggle = screen.getByRole('button', { name: 'Menu' });
    await user.tab();
    expect(toggle).toHaveFocus();
    await user.keyboard('{Enter}');
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('link', { name: 'Home' })).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(toggle).toHaveFocus();
  });

  it('closes when a link is clicked', async () => {
    const user = userEvent.setup();
    renderAt('/');
    await user.click(screen.getByRole('button', { name: 'Menu' }));
    expect(screen.getByRole('button', { name: /menu/i })).toHaveAttribute('aria-expanded', 'true');
    await user.click(screen.getByRole('link', { name: 'Stats' }));
    expect(screen.getByRole('heading', { level: 2, name: 'Stats' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Menu' })).toHaveAttribute('aria-expanded', 'false');
  });

  it('closes when a link is opened with Enter', async () => {
    const user = userEvent.setup();
    renderAt('/');
    await user.tab();
    await user.keyboard('{Enter}'); // open, focus on Home
    await user.tab(); // Games
    await user.keyboard('{Enter}');
    expect(screen.getByRole('heading', { level: 2, name: 'Games' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Menu' })).toHaveAttribute('aria-expanded', 'false');
  });
});
