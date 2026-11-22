import { render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { createMemoryRouter } from 'react-router';
import { RouterProvider } from 'react-router/dom';
import { env } from './env';
import { linkedPackages } from './index';
import { AppProviders } from './providers/AppProviders';
import { routes } from './routes';
import { useSessionStore } from './session/store';

beforeEach(() => {
  useSessionStore.setState({ status: 'signed_in' }); // fake signed-in session: the guard lets pages render (S-1.6.4)
});

function renderApp() {
  // The shell's playgroup switcher (S-2.4.5) needs the query and API providers.
  return render(
    <AppProviders>
      <RouterProvider router={createMemoryRouter(routes, { initialEntries: ['/'] })} />
    </AppProviders>,
  );
}

describe('App shell', () => {
  it('renders the banner landmark containing the MTG Stats heading', () => {
    renderApp();
    const banner = screen.getByRole('banner');
    expect(
      within(banner).getByRole('heading', { level: 1, name: 'MTG Stats' }),
    ).toBeInTheDocument();
  });

  it('renders the main landmark', () => {
    renderApp();
    expect(screen.getByRole('main')).toBeInTheDocument();
  });

  it('renders the contentinfo landmark as the footer placeholder', () => {
    renderApp();
    expect(screen.getByRole('contentinfo')).toBeInTheDocument();
  });

  // The home page is the playgroup home since S-2.4.5, so the S-1.6.1 scaffold lines ("Packages:",
  // "API:") are gone from the UI. The same facts are checked at the module level.
  it('links the three workspace packages at runtime', () => {
    expect(linkedPackages).toEqual([
      '@mtg-stats/api-client',
      '@mtg-stats/card-utils',
      '@mtg-stats/game-core',
    ]);
  });

  it('has an API base URL', () => {
    expect(env.apiBaseUrl).toMatch(/^https?:\/\//);
  });
});
