import { useEffect } from 'react';
import { Link, Navigate } from 'react-router';
import { errorMessage } from '../errors/message';
import { useActivePlaygroup } from '../playgroups/useActivePlaygroup';

/**
 * `/`: where sign-in lands (S-2.4.5). With playgroups it redirects to the active one's home
 * (`/playgroups/:id`); with none it shows the empty state; while the list loads or fails it keeps
 * the "Home" heading and says so.
 */
export function HomePage() {
  const { status, active, playgroups, error, retry } = useActivePlaygroup();

  useEffect(() => {
    document.title = 'Home · MTG Stats';
  }, []);

  if (status === 'ready' && active) {
    return <Navigate replace to={`/playgroups/${active.id}`} />;
  }

  return (
    <section aria-labelledby="page-title">
      <h2 id="page-title">Home</h2>
      {status === 'loading' ? <p role="status">Loading your playgroups…</p> : null}
      {status === 'error' ? (
        <p role="alert">
          {errorMessage(error)}{' '}
          <button type="button" onClick={retry}>
            Retry
          </button>
        </p>
      ) : null}
      {status === 'ready' && playgroups.length === 0 ? (
        <section aria-labelledby="no-playgroups-title">
          <h3 id="no-playgroups-title">You are not in a playgroup yet</h3>
          <p>Start your own, or join one you were invited to.</p>
          <p>
            <Link to="/playgroups">Create a playgroup</Link>
          </p>
          <p>
            <Link to="/playgroups/join">I have an invite</Link>
          </p>
        </section>
      ) : null}
    </section>
  );
}
