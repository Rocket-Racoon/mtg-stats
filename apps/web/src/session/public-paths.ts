import { SIGN_IN_PATH } from './sign-in';

/**
 * Route patterns reachable signed out (S-1.6.4 AC2). The route table puts every other route behind
 * `RequireSession`; session-guard.test.tsx walks the table and fails if a route is neither.
 */
export const PUBLIC_PATHS = [SIGN_IN_PATH, '/about', '/invite/*', '/reset/*', '/join/*'] as const;
