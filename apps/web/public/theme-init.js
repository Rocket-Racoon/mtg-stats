// No content hash in the filename: never serve this file with `Cache-Control: immutable` (S-11.1.5).
// Set the theme before first paint to avoid a flash. Keep in sync with src/theme/store.ts.
// Loaded as an external blocking script so a strict CSP can disallow inline scripts (S-11.6.1).
(function () {
  var theme = null;
  try {
    theme = localStorage.getItem('mtg-stats-theme');
  } catch {
    // Storage unavailable (privacy mode): fall back to the system preference.
  }
  if (theme !== 'light' && theme !== 'dark') {
    theme =
      window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches
        ? 'dark'
        : 'light';
  }
  document.documentElement.setAttribute('data-theme', theme);
})();
