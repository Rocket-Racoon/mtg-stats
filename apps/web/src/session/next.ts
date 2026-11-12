/** Used as `base` to check that a candidate resolves to the same origin. */
const BASE = 'http://same-origin.invalid';

/**
 * Open-redirect guard for the sign-in `?next=` parameter. Returns `raw` only when it is a
 * same-origin relative path (a single leading `/`, no `//`, no `/\`, no backslashes or control
 * characters, not a full URL); otherwise `/`. S-2.1.6's sign-in page uses this after sign-in.
 */
export function safeNext(raw: string | null | undefined): string {
  if (!raw?.startsWith('/')) return '/';
  if (raw.startsWith('//') || raw.startsWith('/\\')) return '/';
  // Browsers strip tabs/newlines and treat "\" as "/", so "/\t/evil.com" would become "//evil.com".
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f\\]/.test(raw)) return '/';
  try {
    if (new URL(raw, BASE).origin !== BASE) return '/';
  } catch {
    return '/';
  }
  return raw;
}
