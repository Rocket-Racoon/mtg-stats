import type { RouteObject } from 'react-router';
import { App } from './App';
import { AboutPage } from './pages/About';
import { RootLayout } from './RootLayout';
import { PublicLayout } from './PublicLayout';
import { HomePage } from './pages/Home';
import { NotFoundPage } from './pages/NotFound';
import { PlaceholderPage } from './pages/Placeholder';

/** The route table, shared by the browser router and tests (createMemoryRouter). */
export const routes: RouteObject[] = [
  // Pathless root above both shells: owns focus-on-navigation across layout switches. S-1.6.3's
  // providers and root errorElement go here too.
  {
    element: <RootLayout />,
    children: [
      // Public shell: no navigation, but the same footer (S-1.6.6). The real sign-in page comes
      // from S-2.1.6; no auth guard until S-1.6.4. Future public routes (/invite, /reset, /join)
      // go here.
      {
        element: <PublicLayout />,
        children: [{ path: '/sign-in', element: <PlaceholderPage title="Sign in" /> }],
      },
      {
        path: '/',
        element: <App />,
        children: [
          { index: true, element: <HomePage /> },
          { path: 'games', element: <PlaceholderPage title="Games" /> },
          { path: 'decks', element: <PlaceholderPage title="Decks" /> },
          { path: 'stats', element: <PlaceholderPage title="Stats" /> },
          { path: 'collection', element: <PlaceholderPage title="Collection" /> },
          { path: 'leagues', element: <PlaceholderPage title="Leagues" /> },
          // /about is intentionally under the App shell and is a PUBLIC route: S-1.6.4's auth
          // guard must not wrap it (the legal notice must be reachable signed out).
          { path: 'about', element: <AboutPage /> },
          { path: '*', element: <NotFoundPage /> },
        ],
      },
    ],
  },
];
