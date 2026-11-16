import type { TokenStore } from '@mtg-stats/api-client';

let accessToken: string | null = null;

/**
 * Web token store (decision B3): the access token lives in memory only. There is no
 * `getRefreshToken`, so the client refreshes in cookie mode with the HttpOnly refresh cookie.
 */
export const memoryTokenStore: TokenStore = {
  getAccessToken: () => accessToken,
  setTokens: ({ accessToken: token }) => {
    accessToken = token;
  },
  clear: () => {
    accessToken = null;
  },
};

/** Call after sign-in with the access token from the login response. */
export function setAccessToken(token: string | null): void {
  accessToken = token;
}
