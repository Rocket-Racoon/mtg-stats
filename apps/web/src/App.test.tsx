import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { App } from './App';

describe('App shell', () => {
  it('renders the banner landmark containing the MTG Stats heading', () => {
    render(<App />);
    const banner = screen.getByRole('banner');
    expect(
      within(banner).getByRole('heading', { level: 1, name: 'MTG Stats' }),
    ).toBeInTheDocument();
  });

  it('renders the main landmark', () => {
    render(<App />);
    expect(screen.getByRole('main')).toBeInTheDocument();
  });

  it('renders the contentinfo landmark as the footer placeholder', () => {
    render(<App />);
    expect(screen.getByRole('contentinfo')).toBeInTheDocument();
  });

  it('renders the three workspace package names, proving the workspace link at runtime', () => {
    render(<App />);
    const main = screen.getByRole('main');
    expect(main).toHaveTextContent('@mtg-stats/api-client');
    expect(main).toHaveTextContent('@mtg-stats/card-utils');
    expect(main).toHaveTextContent('@mtg-stats/game-core');
  });

  it('renders the API base URL inside the main landmark', () => {
    render(<App />);
    expect(screen.getByRole('main')).toHaveTextContent(/API: https?:\/\//);
  });
});
