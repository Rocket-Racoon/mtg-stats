import type { ApiClient } from '@mtg-stats/api-client';
import { readTokens } from './secureStorage';
import { useSessionStore } from './store';

let inFlight: Promise<void> | null = null;
let started = false;

/** Test seam: lets the next `restoreSession` run again (it runs once per app process). */
export function resetRestoreForTests(): void {
  inFlight = null;
  started = false;
}

/**
 * Restores the session at launch (S-2.1.7 AC6). No stored refresh token: signed out, no network
 * call. Otherwise both tokens load into memory and one body-mode refresh runs: true gives
 * signed-in, a rejection clears everything and gives signed-out, and a transient failure (offline,
 * 5xx) keeps the tokens and stays signed in. A second call while one runs (StrictMode) shares the
 * same promise. Never rejects and never logs.
 */
export function restoreSession(client: ApiClient): Promise<void> {
  // Once per app process: a remount after the restore finished must not sign the user out again.
  if (inFlight) return inFlight;
  if (started) return Promise.resolve();
  started = true;
  const run = (async () => {
    const store = useSessionStore.getState();
    // A fresh launch always starts from 'restoring' with empty memory (no pending link yet).
    useSessionStore.setState({
      status: 'restoring',
      accessToken: null,
      refreshToken: null,
      pendingHref: null,
    });
    const stored = await readTokens();
    if (stored.refreshToken === null) {
      store.setStatus('signed-out');
      return;
    }
    useSessionStore.setState({
      accessToken: stored.accessToken,
      refreshToken: stored.refreshToken,
    });
    let outcome: 'signed-in' | 'signed-out';
    try {
      outcome = (await client.refreshSession()) ? 'signed-in' : 'signed-out';
    } catch {
      outcome = 'signed-in'; // transient: keep the tokens, the next request retries the refresh
    }
    if (useSessionStore.getState().status !== 'restoring') return;
    if (outcome === 'signed-in') store.setStatus('signed-in');
    else await store.signOut();
  })();
  inFlight = run;
  const release = () => {
    if (inFlight === run) inFlight = null;
  };
  void run.then(release, release);
  return run;
}
