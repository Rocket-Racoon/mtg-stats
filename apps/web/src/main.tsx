import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { createBrowserRouter } from 'react-router';
import { routes } from './routes';
import { api } from './api';
import './theme/theme.css';
import './App.css';
import { AppRoot } from './providers/AppRoot';
import { restoreSession } from './session/restore';
import { startSignOutSync } from './session/sign-out';
import { startThemeSync } from './theme/store';

const container = document.getElementById('root');
if (!container) {
  throw new Error('Root element #root not found');
}

startThemeSync();
startSignOutSync();

// Resolve the 'restoring' session status once, before anything protected renders (S-2.1.6 AC5).
void restoreSession(api);

const router = createBrowserRouter(routes);

createRoot(container).render(
  <StrictMode>
    <AppRoot router={router} />
  </StrictMode>,
);
