import type { QueryClient } from '@tanstack/react-query';
import { RouterProvider, type RouterProviderProps } from 'react-router/dom';
import { AppProviders } from './AppProviders';

/** What main.tsx renders: the providers around the router. Tests render the same thing. */
export function AppRoot({
  router,
  queryClient,
}: {
  router: RouterProviderProps['router'];
  queryClient?: QueryClient;
}) {
  return (
    <AppProviders {...(queryClient ? { queryClient } : {})}>
      <RouterProvider router={router} />
    </AppProviders>
  );
}
