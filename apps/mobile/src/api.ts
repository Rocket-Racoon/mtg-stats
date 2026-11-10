import { apiOrigin, createApiClient } from '@mtg-stats/api-client';
import { env } from './env';

// The generated operation paths already start with /api/v1, so the client takes the origin only.
export const api = createApiClient({ baseUrl: apiOrigin(env.apiBaseUrl) });
