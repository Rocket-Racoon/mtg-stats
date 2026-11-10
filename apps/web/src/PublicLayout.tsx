import { Outlet } from 'react-router';
import { Footer } from './Footer';

/**
 * Layout for public routes outside the app shell (sign-in, invite, reset, join): content plus the
 * same footer, so the legal notice is on every page (S-1.6.6 AC1). No navigation or header.
 */
export function PublicLayout() {
  return (
    <div className="shell shell-public">
      <main id="main" tabIndex={-1} className="shell-main">
        <Outlet />
      </main>
      <Footer />
    </div>
  );
}
