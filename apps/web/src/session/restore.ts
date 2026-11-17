import type { ApiClient } from '@mtg-stats/api-client';
import { useSessionStore } from './store';
import { clearLogoutPending, isLogoutPending } from './pending-logout';
import { logoutCall } from './logout-hooks';

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
      if (isLogoutPending()) {
        // An earlier sign-out could not reach the server: finish it instead of refreshing, and
        // stay signed out. The flag is cleared only once the server confirmed.
        try {
          await logoutCall(client);
          clearLogoutPending();
        } catch {
          // Still pending: the next load retries.
        }
      } else {
        restored = await client.refreshSession();
        if (restored && isLogoutPending()) {
          // A sign-out in another tab set the flag while this refresh was queued: honour it.
          restored = false;
          try {
            await logoutCall(client);
            clearLogoutPending();
          } catch {
            // Still pending: the next load retries.
          }
        }
      }
    } catch {
      restored = false; // a transient failure: treat as signed out; the user can sign in again
    }
    const store = useSessionStore.getState();
    // This guard protects only the final status write. The client's side effects (clear() and
    // onSessionExpired, or setTokens) already ran inside refreshSession(), so a sign-in that races
    // the restore can still be undone; the pages therefore wait for the restore (see InvitePage).
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
