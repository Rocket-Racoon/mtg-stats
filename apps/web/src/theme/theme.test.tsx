import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { THEME_STORAGE_KEY, resolveTheme, startThemeSync, useThemeStore } from './store';
import { ThemeToggle } from './ThemeToggle';

type Listener = (e: { matches: boolean }) => void;

/** Installs a controllable matchMedia; returns a function to flip the system scheme. */
function mockSystem(initialDark: boolean) {
  let dark = initialDark;
  const listeners = new Set<Listener>();
  vi.stubGlobal(
    'matchMedia',
    vi.fn(() => ({
      get matches() {
        return dark;
      },
      addEventListener: (_: string, l: Listener) => listeners.add(l),
      removeEventListener: (_: string, l: Listener) => listeners.delete(l),
    })),
  );
  return (next: boolean) => {
    dark = next;
    listeners.forEach((l) => {
      l({ matches: next });
    });
  };
}

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute('data-theme');
  useThemeStore.setState({ preference: null, systemDark: false });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('theme store (S-1.6.5 AC1)', () => {
  it('uses dark on first load when the system is dark and nothing is stored', () => {
    mockSystem(true);
    const stop = startThemeSync();
    expect(document.documentElement.dataset.theme).toBe('dark');
    stop();
  });

  it('uses light when the system is light', () => {
    mockSystem(false);
    const stop = startThemeSync();
    expect(document.documentElement.dataset.theme).toBe('light');
    stop();
  });

  it('follows live system changes while there is no stored choice', () => {
    const flip = mockSystem(false);
    const stop = startThemeSync();
    act(() => {
      flip(true);
    });
    expect(document.documentElement.dataset.theme).toBe('dark');
    stop();
  });

  it('a stored choice overrides the system, including later system changes', () => {
    const flip = mockSystem(true);
    useThemeStore.getState().setPreference('light');
    const stop = startThemeSync();
    expect(document.documentElement.dataset.theme).toBe('light');
    act(() => {
      flip(false);
      flip(true);
    });
    expect(document.documentElement.dataset.theme).toBe('light');
    stop();
  });

  it('persists the choice in localStorage', () => {
    useThemeStore.getState().setPreference('dark');
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark');
  });

  it('still switches theme when localStorage throws', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    useThemeStore.getState().setPreference('dark');
    expect(resolveTheme(useThemeStore.getState())).toBe('dark');
  });

  it('resolveTheme prefers the explicit choice over the system', () => {
    expect(resolveTheme({ preference: 'light', systemDark: true })).toBe('light');
    expect(resolveTheme({ preference: null, systemDark: true })).toBe('dark');
  });
});

describe('ThemeToggle', () => {
  it('has an accessible name, exposes state and toggles with the keyboard', async () => {
    mockSystem(true);
    useThemeStore.setState({ preference: null, systemDark: true });
    const stop = startThemeSync();
    const user = userEvent.setup();
    render(<ThemeToggle />);
    const button = screen.getByRole('button', { name: 'Dark mode' });
    expect(button).toHaveAttribute('aria-pressed', 'true');

    await user.tab();
    expect(button).toHaveFocus();
    await user.keyboard('{Enter}');
    expect(button).toHaveAttribute('aria-pressed', 'false');
    expect(document.documentElement.dataset.theme).toBe('light');
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('light');

    await user.keyboard(' ');
    expect(button).toHaveAttribute('aria-pressed', 'true');
    expect(document.documentElement.dataset.theme).toBe('dark');
    stop();
  });
});
