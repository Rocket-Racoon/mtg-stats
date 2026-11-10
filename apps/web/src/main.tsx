import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { createBrowserRouter } from 'react-router';
import { RouterProvider } from 'react-router/dom';
import { routes } from './routes';
// Kept until S-1.5.3 hooks import `api`; proves the client bundles (S-1.5.2 AC4).
import './api';
import './theme/theme.css';
import './App.css';
import { startThemeSync } from './theme/store';

const container = document.getElementById('root');
if (!container) {
  throw new Error('Root element #root not found');
}

startThemeSync();

const router = createBrowserRouter(routes);

createRoot(container).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
