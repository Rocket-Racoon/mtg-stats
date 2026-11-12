import { useEffect, useRef } from 'react';
import { Outlet, useLocation } from 'react-router';

/**
 * Pathless root route above both shells (App and PublicLayout). It owns "move focus to #main on
 * pathname change" so focus survives a switch between layouts (a shell unmounting would otherwise
 * drop focus to <body>). The root errorElement is set on this route in routes.tsx; the providers
 * wrap RouterProvider in main.tsx.
 */
export function RootLayout() {
  const { pathname } = useLocation();
  const previous = useRef(pathname);

  // Not on first load, and not on search-param or hash changes.
  useEffect(() => {
    if (previous.current !== pathname) {
      previous.current = pathname;
      document.getElementById('main')?.focus({ preventScroll: true });
    }
  }, [pathname]);

  return <Outlet />;
}
