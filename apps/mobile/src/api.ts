import { apiOrigin, createApiClient } from '@mtg-stats/api-client';
import { env } from './env';

// The generated operation paths already start with /api/v1, so the client takes the origin only.
export const api = createApiClient({ baseUrl: apiOrigin(env.apiBaseUrl) });

/** Liveness probe: a typed call that proves the client builds and type-checks in the app. */
export function fetchLiveness() {
  return api.GET('/api/v1/health/live');
}
