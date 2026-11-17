import type { ApiClient } from '@mtg-stats/api-client';
import { api } from '../api';
import { useSessionStore } from './store';
import { resetUserStores } from './storeRegistry';

/** Bound on the best-effort server call, so offline never hangs the UI. */
export const SIGN_OUT_TIMEOUT_MS = 4000;

export interface SignOutOptions {
  /** Also revoke every other device first (`POST /auth/logout-all`). */
  everywhere?: boolean;
  /** Defaults to the app client; tests inject one. */
  client?: ApiClient | null;
  timeoutMs?: number;
}

/**
 * Signs this device out (S-2.1.12). The server call is best effort and bounded by a timeout:
 * `/auth/logout` with the refresh token, or `/auth/logout-all` when `everywhere`. Whatever happens
 * to it, the tokens (memory and expo-secure-store), the query cache and every registered
 * user-scoped store are cleared and the status becomes `signed-out`.
 *
 * Exception: with `everywhere`, a failed call rejects (with the ApiError) and nothing is cleared,
 * so the user is not told every device was signed out when it was not. The caller offers a retry
 * or a plain this-device `signOut()`. The timeout races the whole call (a hung token refresh
 * included); an abandoned call's late settle is swallowed and cannot re-sign-in (`setTokens`
 * ignores writes once signed out).
 * Concurrent calls share nothing; the store reset is idempotent.
 */
export async function signOut(options: SignOutOptions = {}): Promise<void> {
  const { everywhere = false, timeoutMs = SIGN_OUT_TIMEOUT_MS } = options;
  const client = options.client === undefined ? api : options.client;
  const state = useSessionStore.getState();
  const refreshToken = state.refreshToken;

  if (client && state.status === 'signed-in') {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    // The bound covers the whole call, including a token refresh the middleware may start first
    // (it carries no abort signal), so the race is against the timer, not only the abort.
    const timeout = new Promise<never>((_, reject) => {
      timer = setTimeout(() => {
        controller.abort();
        reject(new Error('sign-out timed out'));
      }, timeoutMs);
    });
    try {
      let call: Promise<unknown> | null = null;
      if (everywhere) {
        call = client.POST('/api/v1/auth/logout-all', { signal: controller.signal });
      } else if (refreshToken) {
        call = client.POST('/api/v1/auth/logout', {
          body: { refresh_token: refreshToken },
          signal: controller.signal,
        });
      }
      if (call) {
        // An abandoned call may settle late; swallow it so it is never an unhandled rejection.
        call.catch(() => undefined);
        await Promise.race([call, timeout]);
      }
    } catch (error) {
      if (everywhere) throw error;
      // Best effort: offline, timeout or any server error still signs out locally.
    } finally {
      clearTimeout(timer);
      timeout.catch(() => undefined);
    }
  }
  await clearLocalSession();
}

/** Clears tokens, caches and user-scoped stores, and marks the session signed out. */
export async function clearLocalSession(): Promise<void> {
  useSessionStore.getState().setPendingHref(null);
  useSessionStore.getState().setNotice(null);
  resetUserStores();
  await useSessionStore.getState().signOut();
}
