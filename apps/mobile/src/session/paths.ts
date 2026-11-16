/** The path a signed-in user lands on. */
export const LANDING_PATH = '/play';

const PUBLIC_EXACT = new Set(['/', '/sign-in', '/have-invite', '/about']);
const PUBLIC_PREFIXES = ['/invite/', '/reset/'];

/** Paths reachable signed out: they are never kept as a pending deep link. */
export function isPublicPath(path: string): boolean {
  return PUBLIC_EXACT.has(path) || PUBLIC_PREFIXES.some((prefix) => path.startsWith(prefix));
}

/**
 * True for a launch URL worth restoring after the session settles: an in-app path that is neither
 * public nor the landing path.
 */
export function isPendingCandidate(path: string): boolean {
  return (
    path !== LANDING_PATH && !isPublicPath(path) && path.startsWith('/') && !path.startsWith('//')
  );
}
