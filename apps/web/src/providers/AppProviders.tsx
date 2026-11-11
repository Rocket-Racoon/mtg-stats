import { useState, type ReactNode } from 'react';
import { MutationCache, type QueryClient } from '@tanstack/react-query';
import { QueryClientProvider, createQueryClient } from '@mtg-stats/api-client';
import { errorMessage } from '../errors/message';
import { ErrorBoundary } from '../errors/ErrorBoundary';
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
}

export function AppProviders({ children, queryClient }: AppProvidersProps) {
  const [created] = useState(createAppQueryClient);
  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient ?? created}>
        {children}
        <Toaster />
      </QueryClientProvider>
    </ErrorBoundary>
  );
}
