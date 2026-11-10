export const PACKAGE_NAME = '@mtg-stats/api-client';

export { createApiClient } from './client';
export type { ApiClient, ApiClientOptions, ApiMethod } from './client';
export { apiOrigin } from './origin';
export { ApiError, NETWORK_ERROR, UNEXPECTED_RESPONSE } from './errors';
export type { ApiErrorDetails, ApiErrorInit } from './errors';
export type { components, operations, paths } from './generated/schema';
export type { Middleware } from 'openapi-fetch';
