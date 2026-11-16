import type { ApiClient } from '@mtg-stats/api-client';
import { useSessionStore } from './store';

let inFlight: Promise<void> | null = null;

/**
 * Restores the session on page load (S-2.1.6 AC5): one cookie refresh through the API client, so
 * the access token ends up in memory only. Resolves the store's 'restoring' status to
 * 'signed_in' (refresh succeeded) or 'signed_out' (rejected or failed). Called once from
 * main.tsx; a second call while one is running (StrictMode, tests) shares the same promise.
 * Never rejects and never logs.
 */
export function restoreSession(client: ApiClient): Promise<void> {
  if (inFlight) return inFlight;
  const run = (async () => {
    let restored = false;
    try {
      restored = await client.refreshSession();
    } catch {
      restored = false; // a transient failure: treat as signed out; the user can sign in again
    }
    const store = useSessionStore.getState();
    // Only settle a session that is still restoring: a sign-in that raced it wins.
    if (store.status !== 'restoring') return;
    if (restored) store.setStatus('signed_in');
    else store.signOut();
  })();
  inFlight = run;
  const release = () => {
    if (inFlight === run) inFlight = null;
  };
  void run.then(release, release);
  return run;
}
