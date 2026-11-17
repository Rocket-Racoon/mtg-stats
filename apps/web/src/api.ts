import { apiOrigin, createApiClient } from '@mtg-stats/api-client';
import { env } from './env';
import { handleSessionExpired } from './session/sign-out';
import { memoryTokenStore } from './session/tokens';

// The generated operation paths already start with /api/v1, so the client takes the origin only.
export const api = createApiClient({
  baseUrl: apiOrigin(env.apiBaseUrl),
  tokenStore: memoryTokenStore,
  onSessionExpired: handleSessionExpired,
});
