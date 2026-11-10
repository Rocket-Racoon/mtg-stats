import { QueryClient, type QueryClientConfig } from '@tanstack/react-query';
import { ApiError, NETWORK_ERROR } from './errors';

/** A failed query is retried at most this many times. */
export const MAX_RETRIES = 2;

/**
 * Retry rule for queries: only transient failures are retried, at most `MAX_RETRIES` times.
 * 4xx, `unexpected_response` on a 2xx and non-ApiError (programming) errors are not.
 */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (failureCount >= MAX_RETRIES) return false;
  if (!(error instanceof ApiError)) return false;
  if (error.status >= 400 && error.status < 500) return false;
  return error.code === NETWORK_ERROR || error.status >= 500;
}

/**
 * Default options: queries retry per `shouldRetry` with TanStack's exponential delay and are
 * fresh for 30 s; mutations never retry (they are not idempotent).
 */
export function defaultQueryClientConfig(): QueryClientConfig {
  return {
    defaultOptions: {
      queries: { retry: shouldRetry, staleTime: 30_000 },
      mutations: { retry: false },
    },
  };
}

function defined<T extends object>(values: T | undefined): Partial<T> {
  return Object.fromEntries(
    Object.entries(values ?? {}).filter(([, value]) => value !== undefined),
  ) as Partial<T>;
}

/**
 * Creates the app's QueryClient. `overrides` are merged over the defaults, per section;
 * `undefined` values are ignored, so `retry: undefined` keeps the default rule.
 */
export function createQueryClient(overrides: QueryClientConfig = {}): QueryClient {
  const base = defaultQueryClientConfig();
  return new QueryClient({
    ...base,
    ...overrides,
    defaultOptions: {
      ...base.defaultOptions,
      ...overrides.defaultOptions,
      queries: { ...base.defaultOptions?.queries, ...defined(overrides.defaultOptions?.queries) },
      mutations: {
        ...base.defaultOptions?.mutations,
        ...defined(overrides.defaultOptions?.mutations),
      },
    },
  });
}
