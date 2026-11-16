import type { TokenStore } from '@mtg-stats/api-client';
import { deleteTokens } from './secureStorage';
import { useSessionStore } from './store';

/**
 * The API client's token store in body mode (S-2.1.7): it has `getRefreshToken`, so the client
 * refreshes with `{ refresh_token }` in the request body and rotates both tokens through
 * `setTokens`. Reads come from memory; writes update memory first, then expo-secure-store
 * (failures are swallowed there).
 */
export const secureTokenStore: TokenStore = {
  getAccessToken: () => useSessionStore.getState().accessToken,
  getRefreshToken: () => useSessionStore.getState().refreshToken,
  setTokens: ({ accessToken, refreshToken }) =>
    useSessionStore.getState().setTokens({ accessToken, refreshToken }),
  clear: async () => {
    useSessionStore.setState({ accessToken: null, refreshToken: null });
    await deleteTokens();
  },
};

/** Called by the client once a refresh is rejected, after `clear()`. */
export function onSessionExpired(): void {
  void useSessionStore.getState().signOut();
}
