import { create } from 'zustand';
import type { Theme } from './tokens';

/** localStorage key. index.html's pre-render script reads the same key. */
export const THEME_ITEM_NAME = 'mtg-stats-theme';
const DARK_QUERY = '(prefers-color-scheme: dark)';

export function readStoredTheme(): Theme | null {
  try {
    const value = localStorage.getItem(THEME_ITEM_NAME);
    return value === 'light' || value === 'dark' ? value : null;
  } catch {
    return null; // storage blocked or unavailable
  }
}

function writeStoredTheme(theme: Theme): void {
  try {
    localStorage.setItem(THEME_ITEM_NAME, theme);
  } catch {
    // Works without storage: the choice just is not remembered.
  }
}

export function systemPrefersDark(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia(DARK_QUERY).matches;
}

interface ThemeState {
  /** The user's explicit choice, or null to follow the system. */
  preference: Theme | null;
  systemDark: boolean;
  setPreference: (theme: Theme) => void;
  setSystemDark: (dark: boolean) => void;
}

export const useThemeStore = create<ThemeState>()((set) => ({
  preference: readStoredTheme(),
  systemDark: systemPrefersDark(),
  setPreference: (theme) => {
    writeStoredTheme(theme);
    set({ preference: theme });
  },
  setSystemDark: (dark) => {
    set({ systemDark: dark });
  },
}));

export function resolveTheme(state: Pick<ThemeState, 'preference' | 'systemDark'>): Theme {
  return state.preference ?? (state.systemDark ? 'dark' : 'light');
}

export function useResolvedTheme(): Theme {
  return useThemeStore(resolveTheme);
}

/**
 * Keeps <html data-theme> in sync with the store and follows live system changes.
 * Returns a cleanup function.
 */
export function startThemeSync(): () => void {
  const apply = () => {
    document.documentElement.dataset.theme = resolveTheme(useThemeStore.getState());
  };
  apply();
  const unsubscribe = useThemeStore.subscribe(apply);

  let mql: MediaQueryList | null = null;
  const onChange = (event: MediaQueryListEvent) => {
    useThemeStore.getState().setSystemDark(event.matches);
  };
  if (typeof window.matchMedia === 'function') {
    mql = window.matchMedia(DARK_QUERY);
    mql.addEventListener('change', onChange);
    useThemeStore.getState().setSystemDark(mql.matches);
  }
  return () => {
    unsubscribe();
    mql?.removeEventListener('change', onChange);
  };
}
