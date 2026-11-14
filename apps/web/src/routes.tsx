import type { RouteObject } from 'react-router';
import { App } from './App';
import { AboutPage } from './pages/About';
import { RouteErrorFallback } from './errors/RouteErrorFallback';
import { RootLayout } from './RootLayout';
import { PublicLayout } from './PublicLayout';
import { HomePage } from './pages/Home';
import { NotFoundPage } from './pages/NotFound';
import { PlaceholderPage } from './pages/Placeholder';
import { SharedLinkPage } from './pages/SharedLinkPage';
import { RequireSession } from './session/RequireSession';
import { SIGN_IN_PATH } from './session/sign-in';

/** The route table, shared by the browser router and tests (createMemoryRouter). */
export const routes: RouteObject[] = [
  // Pathless root above both shells: owns focus-on-navigation across layout switches and the root
  // errorElement (render errors in routes). The providers wrap RouterProvider in main.tsx.
  {
    errorElement: <RouteErrorFallback />,
    element: <RootLayout />,
    children: [
      // Public shell: no navigation, but the same footer (S-1.6.6). Nothing here is guarded; the
      // paths must match PUBLIC_PATHS (session/session-guard.test.tsx enforces it). The real sign-in page comes
      // from S-2.1.6. The invite/reset/join pages are placeholders until S-2.1.6, S-2.1.9 and S-6.2.3;
      // S-1.6.7 gives them the "Open in app" button.
      {
        element: <PublicLayout />,
        children: [
          { path: SIGN_IN_PATH, element: <PlaceholderPage title="Sign in" /> },
          { path: '/invite/*', element: <SharedLinkPage kind="invite" title="Invite" /> },
          { path: '/reset/*', element: <SharedLinkPage kind="reset" title="Reset password" /> },
          { path: '/join/*', element: <SharedLinkPage kind="join" title="Join" /> },
        ],
      },
      {
        path: '/',
        element: <App />,
        children: [
          // /about is intentionally under the App shell and PUBLIC: it sits outside RequireSession
          // so the legal notice is reachable signed out.
          { path: 'about', element: <AboutPage /> },
          // Everything else is behind the guard. The catch-all is protected on purpose: a
          // signed-out visitor is sent to sign-in for any unknown path, so a 404 versus a redirect
          // never reveals which private paths exist.
          {
            element: <RequireSession />,
            children: [
              { index: true, element: <HomePage /> },
              { path: 'games', element: <PlaceholderPage title="Games" /> },
              { path: 'decks', element: <PlaceholderPage title="Decks" /> },
              { path: 'stats', element: <PlaceholderPage title="Stats" /> },
              { path: 'collection', element: <PlaceholderPage title="Collection" /> },
              { path: 'leagues', element: <PlaceholderPage title="Leagues" /> },
              { path: '*', element: <NotFoundPage /> },
            ],
          },
        ],
      },
    ],
  },
];
