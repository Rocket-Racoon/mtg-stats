/**
 * Pending-logout flag (S-2.1.12). When POST /auth/logout fails (offline, 5xx, timeout) the
 * HttpOnly refresh cookie survives. We remember that fact in localStorage so the next page load
 * retries the logout instead of refreshing. The value is a plain boolean marker, NOT a secret:
 * decision B3 forbids tokens in Web Storage, and none is stored. Every access tolerates missing
 * or blocked storage. Read on load only (no cross-tab sync).
 */
// The name of a storage entry, not a secret (the hook's `key = '...'` pattern is a false positive).
export const LOGOUT_PENDING_KEY = 'mtgstats-logout-pending'; // secrets-check: allow

export function setLogoutPending(): void {
  try {
    localStorage.setItem(LOGOUT_PENDING_KEY, '1');
  } catch {
    // Storage missing or blocked: the retry is lost, nothing else breaks.
  }
}

export function isLogoutPending(): boolean {
  try {
    return localStorage.getItem(LOGOUT_PENDING_KEY) !== null;
  } catch {
    return false;
  }
}

export function clearLogoutPending(): void {
  try {
    localStorage.removeItem(LOGOUT_PENDING_KEY);
  } catch {
    // See above.
  }
}
