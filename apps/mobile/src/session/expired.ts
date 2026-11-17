import { isPendingCandidate } from './paths';
import { useSessionStore } from './store';
import { resetUserStores } from './storeRegistry';

export const SESSION_EXPIRED_MESSAGE = 'Your session has expired';

let currentPath: string | null = null;

/** The root navigator reports the pathname here, so the expiry handler knows the open screen. */
export function setCurrentPath(path: string | null): void {
  currentPath = path;
}

function safeDecode(segment: string): string {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}

/** Encodes each segment once (the router may report decoded or encoded paths). */
export function toPendingHref(pathname: string): string {
  return pathname
    .split('/')
    .map((segment) => encodeURIComponent(safeDecode(segment)))
    .join('/');
}

/**
 * The API client's `onSessionExpired` (S-1.5.3): the refresh was rejected and the tokens are
 * already cleared. While signed in, the open protected screen becomes the pending deep link and
 * the sign-in screen shows the expiry message. Otherwise (launch restore) it just signs out.
 * Clears the same stores as an explicit sign-out. Never throws.
 */
export function handleSessionExpired(): void {
  const store = useSessionStore.getState();
  const wasSignedIn = store.status === 'signed-in';
  const path = currentPath;
  resetUserStores();
  void store.signOut();
  if (!wasSignedIn) return;
  store.setNotice('session-expired');
  if (path !== null && isPendingCandidate(path)) store.setPendingHref(toPendingHref(path));
}
