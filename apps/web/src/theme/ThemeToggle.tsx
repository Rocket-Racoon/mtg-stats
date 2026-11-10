import { useResolvedTheme, useThemeStore } from './store';

/** Light/dark switch. A native button, so Enter and Space work; state is exposed via aria-pressed. */
export function ThemeToggle() {
  const theme = useResolvedTheme();
  const setPreference = useThemeStore((s) => s.setPreference);
  const dark = theme === 'dark';
  return (
    <button
      type="button"
      className="theme-toggle"
      aria-pressed={dark}
      onClick={() => {
        setPreference(dark ? 'light' : 'dark');
      }}
    >
      Dark mode
    </button>
  );
}
