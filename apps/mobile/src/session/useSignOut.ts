import { useMutation } from '@mtg-stats/api-client';
import { signOut } from './signOut';

/**
 * Sign out of this device (S-2.1.12): best-effort `/auth/logout`, then local clear. Never rejects.
 * Hand-written mutation over `signOut`, which calls the generated client.
 */
export function useSignOut() {
  return useMutation({ meta: { errorToast: false }, mutationFn: () => signOut() });
}

/**
 * Sign out everywhere: `POST /auth/logout-all`, then this device signs out. Rejects with the
 * ApiError (nothing cleared) when the call fails, so the screen can offer a retry.
 */
export function useSignOutEverywhere() {
  return useMutation({
    meta: { errorToast: false },
    mutationFn: () => signOut({ everywhere: true }),
  });
}
