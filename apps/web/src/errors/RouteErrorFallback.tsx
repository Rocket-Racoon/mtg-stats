import { useRouteError } from 'react-router';
import { ErrorFallback } from './ErrorFallback';

/** The root route's errorElement. */
export function RouteErrorFallback() {
  return <ErrorFallback error={useRouteError()} />;
}
