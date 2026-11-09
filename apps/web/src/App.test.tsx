import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { routes } from './routes';

function renderApp() {
  return render(<RouterProvider router={createMemoryRouter(routes, { initialEntries: ['/'] })} />);
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

  it('renders the three workspace package names, proving the workspace link at runtime', () => {
    renderApp();
    const main = screen.getByRole('main');
    expect(main).toHaveTextContent('@mtg-stats/api-client');
    expect(main).toHaveTextContent('@mtg-stats/card-utils');
    expect(main).toHaveTextContent('@mtg-stats/game-core');
  });

  it('renders the API base URL inside the main landmark', () => {
    renderApp();
    expect(screen.getByRole('main')).toHaveTextContent(/API: https?:\/\//);
  });
});
