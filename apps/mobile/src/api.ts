import { apiOrigin, createApiClient, type ApiClient } from '@mtg-stats/api-client';
import { env } from './env';
import { onSessionExpired, secureTokenStore } from './session/secureTokenStore';

/**
 * The app's API client, or `null` when `EXPO_PUBLIC_API_BASE_URL` is missing or malformed. The
 * generated operation paths already start with /api/v1, so the client takes the origin only.
 * `AppProviders` shows the config error screen instead of mounting anything that needs it.
 *
 * Every request is in mobile mode (`X-Client: mobile`): login and register return the refresh
 * token in the body, and the secure token store drives the client's refresh-and-retry (S-2.1.7).
 */
export const api: ApiClient | null = env.ok
  ? createApiClient({
      baseUrl: apiOrigin(env.apiBaseUrl),
      headers: { 'X-Client': 'mobile' },
      tokenStore: secureTokenStore,
      onSessionExpired,
    })
  : null;
