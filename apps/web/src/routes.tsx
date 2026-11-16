import type { RouteObject } from 'react-router';
import { App } from './App';
import { AboutPage } from './pages/About';
import { RouteErrorFallback } from './errors/RouteErrorFallback';
import { RootLayout } from './RootLayout';
import { PublicLayout } from './PublicLayout';
import { HomePage } from './pages/Home';
import { NotFoundPage } from './pages/NotFound';
import { PlaceholderPage } from './pages/Placeholder';
import { InvitePage } from './pages/auth/InvitePage';
import { SignInPage } from './pages/auth/SignInPage';
import { AccountPage } from './pages/AccountPage';
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
      // paths must match PUBLIC_PATHS (session/session-guard.test.tsx enforces it). Sign-in and the
      // invite page are real (S-2.1.6); the reset and join pages are placeholders until S-2.1.9 and
      // S-6.2.3, with the "Open in app" button from S-1.6.7.
      {
        element: <PublicLayout />,
        children: [
          { path: SIGN_IN_PATH, element: <SignInPage /> },
          { path: '/invite/*', element: <InvitePage /> },
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
              { path: 'account', element: <AccountPage /> },
              { path: '*', element: <NotFoundPage /> },
            ],
          },
        ],
      },
    ],
  },
];
