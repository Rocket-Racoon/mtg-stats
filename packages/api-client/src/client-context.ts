import { createContext, createElement, useContext, type ReactNode } from 'react';
import type { ApiClient } from './client';

const ApiClientContext = createContext<ApiClient | null>(null);

export interface ApiClientProviderProps {
  client: ApiClient;
  children?: ReactNode;
}

/** Makes the app's API client available to the hooks. Mounted once by each app. */
export function ApiClientProvider({ client, children }: ApiClientProviderProps) {
  return createElement(ApiClientContext.Provider, { value: client }, children);
}

/** The client given to `ApiClientProvider`. Throws when no provider is mounted. */
export function useApiClient(): ApiClient {
  const client = useContext(ApiClientContext);
  if (client === null) {
    throw new Error('useApiClient must be used inside <ApiClientProvider>.');
  }
  return client;
}
