import { useQuery } from '@tanstack/react-query';
import { useApiClient } from '../client-context';
import { queryKey } from '../query-keys';

/**
 * Worked example: the template for every domain's hooks module (see the README).
 * One key per operation, built with the key factory; the typed client throws `ApiError`.
 */
export const systemKeys = {
  healthLive: () => queryKey('system', 'healthLive'),
  healthReady: () => queryKey('system', 'healthReady'),
};

/** `GET /api/v1/health/live`. */
export function useHealthLive() {
  const client = useApiClient();
  return useQuery({
    queryKey: systemKeys.healthLive(),
    queryFn: ({ signal }) => client.GET('/api/v1/health/live', { signal }),
  });
}

/** `GET /api/v1/health/ready`. A 503 (a dependency is down) surfaces as an `ApiError`. */
export function useHealthReady() {
  const client = useApiClient();
  return useQuery({
    queryKey: systemKeys.healthReady(),
    queryFn: ({ signal }) => client.GET('/api/v1/health/ready', { signal }),
  });
}
