import {
  ApiClientProvider,
  QueryClientProvider,
  createQueryClient,
  type QueryClient,
} from '@mtg-stats/api-client';
import { useEffect, useState, type ReactNode } from 'react';
import { api } from '../api';
import { env } from '../env';
import { registerStoreReset } from '../session/storeRegistry';
import { ConfigErrorScreen } from './ConfigErrorScreen';

/**
 * Mounts the API client and TanStack Query for the whole app. When the environment is invalid it
 * renders the config error screen instead of `children`, so nothing that needs the API mounts.
 * TanStack Query is imported only through `@mtg-stats/api-client` so there is one copy and one
 * React context.
 */
export function AppProviders({
  children,
  queryClient: injected,
}: {
  children: ReactNode;
  /** Tests inject their own client; the default is created once with `createQueryClient()`. */
  queryClient?: QueryClient;
}) {
  const [queryClient] = useState<QueryClient>(() => injected ?? createQueryClient());
  // The query cache is user-scoped: sign-out clears it through the store registry (S-2.1.12).
  useEffect(
    () =>
      registerStoreReset(() => {
        void queryClient.cancelQueries();
        queryClient.clear();
      }),
    [queryClient],
  );
  if (!env.ok || api === null) {
    return <ConfigErrorScreen problem={env.ok ? 'missing' : env.problem} />;
  }
  return (
    <ApiClientProvider client={api}>
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    </ApiClientProvider>
  );
}
