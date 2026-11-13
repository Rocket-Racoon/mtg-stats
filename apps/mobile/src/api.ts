import { apiOrigin, createApiClient, type ApiClient } from '@mtg-stats/api-client';
import { env } from './env';

/**
 * The app's API client, or `null` when `EXPO_PUBLIC_API_BASE_URL` is missing or malformed. The
 * generated operation paths already start with /api/v1, so the client takes the origin only.
 * `AppProviders` shows the config error screen instead of mounting anything that needs it.
 */
export const api: ApiClient | null = env.ok
  ? createApiClient({ baseUrl: apiOrigin(env.apiBaseUrl) })
  : null;
