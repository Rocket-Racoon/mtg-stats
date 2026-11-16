export const PACKAGE_NAME = '@mtg-stats/api-client';

export { createApiClient } from './client';
export type { ApiClient, ApiClientOptions, ApiMethod } from './client';
export { REFRESH_PATH } from './auth';
export type { AuthTokens, TokenStore } from './auth';
export { apiOrigin } from './origin';
export { TOO_MANY_ATTEMPTS_FALLBACK, tooManyAttemptsMessage } from './rate-limit';
export { ApiError, NETWORK_ERROR, UNEXPECTED_RESPONSE, parseRetryAfter } from './errors';
export type { ApiErrorDetails, ApiErrorInit } from './errors';
export type { components, operations, paths } from './generated/schema';
export type { Middleware } from 'openapi-fetch';

export {
  QueryClientProvider,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
export type { QueryClient } from '@tanstack/react-query';

export { DOMAINS, domainKey, queryKey } from './query-keys';
export type { Domain, QueryKey, QueryParams } from './query-keys';
export {
  MAX_RETRIES,
  createQueryClient,
  defaultQueryClientConfig,
  shouldRetry,
} from './query-client';
export { cursorPageOptions } from './pagination';
export type { CursorPageConfig, Page } from './pagination';
export { ApiClientProvider, useApiClient } from './client-context';
export type { ApiClientProviderProps } from './client-context';
export { systemKeys, useHealthLive, useHealthReady } from './hooks/system';
export { authKeys, useInvitePreview, useRegister, useSignIn } from './hooks/auth';
export type { InvitePreview, TokenResponse } from './hooks/auth';
