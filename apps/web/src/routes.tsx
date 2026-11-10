import type { RouteObject } from 'react-router';
import { App } from './App';
import { AboutPage } from './pages/About';
import { PublicLayout } from './PublicLayout';
import { HomePage } from './pages/Home';
import { NotFoundPage } from './pages/NotFound';
import { PlaceholderPage } from './pages/Placeholder';

/** The route table, shared by the browser router and tests (createMemoryRouter). */
export const routes: RouteObject[] = [
  // Public shell: no navigation, but the same footer (S-1.6.6). The real sign-in page comes from
  // S-2.1.6; no auth guard until S-1.6.4. Future public routes (/invite, /reset, /join) go here.
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
      { path: 'about', element: <AboutPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
];
