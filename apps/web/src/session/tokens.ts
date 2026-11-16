import type { TokenStore } from '@mtg-stats/api-client';
import { useSessionStore } from './store';

/**
 * Web token store (decision B3, cookie mode): an adapter over the in-memory session store. There
 * is no `getRefreshToken`, so the client refreshes with the HttpOnly refresh cookie.
 */
export const memoryTokenStore: TokenStore = {
  getAccessToken: () => useSessionStore.getState().accessToken,
  setTokens: ({ accessToken }) => {
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
