import { Navigate, Outlet, useLocation } from 'react-router';
import { useSession } from './session';
import { SIGN_IN_PATH } from './sign-in';

/**
 * Layout route guarding the protected pages (S-1.6.4). Reads only `status` from the session
 * interface: restoring shows a loading state (no redirect, so a reload never bounces a signed-in
 * user), signed out redirects to /sign-in?next=<where they wanted to go>, signed in renders the page.
 */
export function RequireSession() {
  const { status } = useSession();
  const { pathname, search, hash } = useLocation();

  if (pathname === SIGN_IN_PATH) return <Outlet />; // safety net: never redirect to ourselves
  if (status === 'restoring') {
    // No aria-busy: it would suppress the live region's announcement.
    return (
      <div role="status" aria-label="Loading page">
        Loading…
      </div>
    );
  }
  if (status === 'signed_out') {
    // "/" is legal in a query, so keep it literal (AC1: /sign-in?next=/decks). & # + ? = and the
    // rest stay encoded, so the URLSearchParams round trip is exact.
    const next = encodeURIComponent(pathname + search + hash).replaceAll('%2F', '/');
    return <Navigate replace to={`${SIGN_IN_PATH}?next=${next}`} />;
  }
  return <Outlet />;
}
