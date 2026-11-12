/** Used as `base` to parse a candidate as a relative reference. */
const BASE = 'http://same-origin.invalid';

/**
 * Open-redirect guard for the sign-in `?next=` parameter. Returns a normalized same-origin
 * relative path (a single leading `/`, no `//` or `/\`, no backslashes or control characters, not
 * a full URL, and no dot-segments that collapse to `//host`); otherwise `/`. The returned value
 * is the parsed one, so what is checked is what is used. S-2.1.6's sign-in page uses this.
 */
export function safeNext(raw: string | null | undefined): string {
  if (!raw?.startsWith('/')) return '/';
  if (raw.startsWith('//') || raw.startsWith('/\\')) return '/';
  // Browsers strip tabs/newlines and treat "\" as "/", so "/\t/evil.com" would become "//evil.com".
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f\\]/.test(raw)) return '/';
  let url: URL;
  try {
    url = new URL(raw, BASE);
  } catch {
    return '/';
  }
  // "/..//evil.com", "/.//evil.com" and "/%2e%2e//evil.com" normalize to the pathname "//evil.com".
  if (url.origin !== BASE || url.pathname.startsWith('//')) return '/';
  return url.pathname + url.search + url.hash;
}
