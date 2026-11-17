import { useEffect, useState, type ReactNode } from 'react';
import { MutationCache, type QueryClient } from '@tanstack/react-query';
import {
  ApiClientProvider,
  QueryClientProvider,
  createQueryClient,
  type ApiClient,
} from '@mtg-stats/api-client';
import { api } from '../api';
import { errorMessage } from '../errors/message';
import { ErrorBoundary } from '../errors/ErrorBoundary';
import { registerUserStateReset } from '../session/user-state';
import { Toaster } from '../ui/Toaster';
import { useToastStore } from '../ui/toasts';

/** The app's QueryClient: S-1.5.4 defaults plus a toast for every failed mutation. */
export function createAppQueryClient(): QueryClient {
  return createQueryClient({
    mutationCache: new MutationCache({
      onError: (error, _variables, _context, mutation) => {
        // Screens that show the error inline opt out with `meta: { errorToast: false }`.
        if (mutation.meta?.errorToast === false) return;
        useToastStore.getState().push('error', errorMessage(error));
      },
    }),
  });
}

interface AppProvidersProps {
  children: ReactNode;
  /** Tests inject their own client. */
  queryClient?: QueryClient;
  /** Tests inject their own API client. */
  apiClient?: ApiClient;
}

export function AppProviders({ children, queryClient, apiClient }: AppProvidersProps) {
  const [created] = useState(createAppQueryClient);
  const client = queryClient ?? created;
  // Sign-out drops every cached server response: they belong to the user who just left.
  useEffect(
    () =>
      registerUserStateReset(() => {
        void client.cancelQueries();
        client.clear();
      }),
    [client],
  );
  return (
    <ErrorBoundary>
      <QueryClientProvider client={client}>
        <ApiClientProvider client={apiClient ?? api}>
          {children}
          <Toaster />
        </ApiClientProvider>
      </QueryClientProvider>
    </ErrorBoundary>
  );
}
