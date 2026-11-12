/**
 * Route patterns reachable signed out (S-1.6.4 AC2). The route table puts every other route behind
 * `RequireSession`; routes.test.tsx walks the table and fails if a route is neither.
 */
export const PUBLIC_PATHS = ['/sign-in', '/about', '/invite/*', '/reset/*', '/join/*'] as const;
