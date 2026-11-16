/**
 * Routes reachable while signed out, as root Stack screen names. The Reset screen arrives with E-2;
 * its path matches the web reset link `<PUBLIC_WEB_URL>/reset/<token>` (S-1.6.7).
 */
export const PUBLIC_ROUTES = ['invite/[token]', 'reset/[token]'] as const;

/** First path segments of PUBLIC_ROUTES, plus the signed-out stack's own screens. */
const PUBLIC_SEGMENTS = new Set<string>([
  ...PUBLIC_ROUTES.map((name) => name.split('/')[0] ?? name),
  'sign-in',
  'about',
]);

/** True when `pathname` (e.g. `/invite/abc`) needs no session. */
export function isPublicPath(pathname: string): boolean {
  const first = pathname.split('/').filter(Boolean)[0];
  return first === undefined || PUBLIC_SEGMENTS.has(first);
}

/** Root segments of the routes inside the signed-in guard (`(tabs)` group and `join/[code]`). */
const PROTECTED_ROOTS = new Set(['(tabs)', 'join']);

/** True when a root segment (from `useSegments()`) is a route that needs a session. */
export function isProtectedRoot(root: string): boolean {
  return PROTECTED_ROOTS.has(root);
}

/** True when a root segment is a public deep-link screen (Invite, Reset). */
export function isPublicLinkRoot(root: string): boolean {
  return PUBLIC_ROUTES.some((name) => name.split('/')[0] === root);
}
