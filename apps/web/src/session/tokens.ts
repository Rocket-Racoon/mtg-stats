import type { TokenStore } from '@mtg-stats/api-client';
import { useSessionStore } from './store';

/**
 * Web token store (decision B3, cookie mode): an adapter over the in-memory session store. There
 * is no `getRefreshToken`, so the client refreshes with the HttpOnly refresh cookie.
 */
export const memoryTokenStore: TokenStore = {
  getAccessToken: () => useSessionStore.getState().accessToken,
  setTokens: ({ accessToken }) => {
    // A refresh that finishes after sign-out must not bring the token back. The startup restore
    // runs while 'restoring', so it is unaffected.
    if (useSessionStore.getState().status === 'signed_out') return;
    useSessionStore.getState().setAccessToken(accessToken);
  },
  clear: () => {
    useSessionStore.getState().setAccessToken(null);
  },
};

/** Sets (or clears) the access token without changing the session status. */
export function setAccessToken(token: string | null): void {
  useSessionStore.getState().setAccessToken(token);
}
