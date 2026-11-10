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

/**
 * `GET /api/v1/health/ready`. When a dependency is down the API answers 503 with the normal
 * ready body, which surfaces as an `ApiError` (`unexpected_response`); the failed checks are
 * in `error.details`. Does not retry, so a readiness view shows "not ready" at once.
 */
export function useHealthReady() {
  const client = useApiClient();
  return useQuery({
    queryKey: systemKeys.healthReady(),
    retry: false,
    queryFn: ({ signal }) => client.GET('/api/v1/health/ready', { signal }),
  });
}
