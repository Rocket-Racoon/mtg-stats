import { useMutation } from '@tanstack/react-query';
import { REFRESH_LOCK, useApiClient, type ApiClient } from '@mtg-stats/api-client';

/** A hung server must not leave the button or dialog stuck: logout calls give up after this. */
export const LOGOUT_TIMEOUT_MS = 4000;

/** The slice of the Web Locks API used here. */
interface LockManagerLike {
  request: <T>(
    name: string,
    options: { signal: AbortSignal },
    callback: () => Promise<T>,
  ) => Promise<T>;
}

/** Runs `call` with an abort signal that fires on timeout or when `outer` aborts. */
async function withTimeout(
  call: (signal: AbortSignal) => Promise<unknown>,
  outer?: AbortSignal,
): Promise<unknown> {
  const controller = new AbortController();
  const timer = setTimeout(() => {
    controller.abort();
  }, LOGOUT_TIMEOUT_MS);
  const onOuterAbort = () => {
    controller.abort();
  };
  if (outer?.aborted) controller.abort();
  outer?.addEventListener('abort', onOuterAbort);
  try {
    return await call(controller.signal);
  } finally {
    clearTimeout(timer);
    outer?.removeEventListener('abort', onOuterAbort);
  }
}

/**
 * `POST /api/v1/auth/logout`, queued behind any cookie refresh through the same Web Lock the API
 * client uses, so it always sends the newest cookie and cannot interleave with a refresh. The 4 s
 * timeout starts BEFORE the lock is requested and aborts the lock wait too, so a logout queued
 * behind a hung refresh (this tab or another) rejects with an AbortError instead of hanging.
 * Without Web Locks it is a plain call.
 */
export function logoutCall(client: ApiClient, outer?: AbortSignal): Promise<unknown> {
  const locks = (globalThis as { navigator?: { locks?: LockManagerLike } }).navigator?.locks;
  return withTimeout((signal) => {
    const run = () => client.POST('/api/v1/auth/logout', { signal }) as Promise<unknown>;
    if (locks && typeof locks.request === 'function') {
      return locks.request(REFRESH_LOCK, { signal }, run);
    }
    return run();
  }, outer);
}

/**
 * `POST /api/v1/auth/logout` (S-2.1.12): revokes the refresh cookie's token and clears the cookie.
 * Idempotent on the server. The caller signs out locally whatever the outcome, so the error toast
 * is off. The variable is an optional abort signal (the 4 s timeout always applies).
 */
export function useLogout() {
  const client = useApiClient();
  return useMutation({
    meta: { errorToast: false },
    mutationFn: (signal?: AbortSignal): Promise<unknown> => logoutCall(client, signal),
  });
}

/** `POST /api/v1/auth/logout-all`: revokes every refresh token of the caller (all devices). */
export function useLogoutAll() {
  const client = useApiClient();
  return useMutation({
    meta: { errorToast: false },
    mutationFn: (signal?: AbortSignal): Promise<unknown> =>
      withTimeout(
        (s) => client.POST('/api/v1/auth/logout-all', { signal: s }) as Promise<unknown>,
        signal,
      ),
  });
}
