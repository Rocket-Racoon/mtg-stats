import { Navigate, Outlet, useLocation } from 'react-router';
import { useSession } from './session';

/**
 * Layout route guarding the protected pages (S-1.6.4). Reads only `status` from the session
 * interface: restoring shows a loading state (no redirect, so a reload never bounces a signed-in
 * user), signed out redirects to /sign-in?next=<where they wanted to go>, signed in renders the page.
 */
export function RequireSession() {
  const { status } = useSession();
  const { pathname, search, hash } = useLocation();

  if (pathname === '/sign-in') return <Outlet />; // never redirect to ourselves
  if (status === 'restoring') {
    return (
      <div role="status" aria-label="Loading page" aria-busy="true">
        Loading…
      </div>
    );
  }
  if (status === 'signed_out') {
    const next = encodeURIComponent(pathname + search + hash);
    return <Navigate replace to={`/sign-in?next=${next}`} />;
  }
  return <Outlet />;
}
