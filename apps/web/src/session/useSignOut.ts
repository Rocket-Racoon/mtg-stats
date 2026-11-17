import { useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import { useApiClient } from '@mtg-stats/api-client';
import { logoutCall, useLogout, useLogoutAll } from './logout-hooks';
import { setLogoutPending } from './pending-logout';
import { SIGN_IN_PATH } from './sign-in';
import { completeSignOut } from './sign-out';
import { useToastStore } from '../ui/toasts';

export const SIGN_OUT_EVERYWHERE_FAILED =
  "We couldn't sign you out of your other devices. You are still signed in here. Try again, or sign out on this device only.";

export const LOGOUT_PENDING_WARNING =
  "We couldn't reach the server to finish signing out. You are signed out on this device; we'll try again the next time you open the app.";

/** Signs this device out locally, tells the other tabs, and shows /sign-in with focus on it. */
function useFinishSignOut() {
  const navigate = useNavigate();
  return () => {
    completeSignOut();
    void navigate(SIGN_IN_PATH, { replace: true });
  };
}

/** Plain sign-out. On a failed API call the pending flag makes the next load retry the logout. */
function usePlainSignOut() {
  const logout = useLogout();
  const finish = useFinishSignOut();
  const signOut = async () => {
    let failed = false;
    try {
      await logout.mutateAsync(undefined);
    } catch {
      failed = true;
      setLogoutPending();
    }
    finish();
    // After finish(): the sign-out reset clears the toast store, so the warning goes in last.
    if (failed) useToastStore.getState().push('error', LOGOUT_PENDING_WARNING);
  };
  return { signOut, signingOut: logout.isPending };
}

/**
 * "Sign out" (S-2.1.12): best effort. The API call may fail (offline, server error, 4 s timeout);
 * this device signs out regardless and the user is warned. Once started it cannot be cancelled:
 * only the logout-all flow (`useSignOutEverywhere().cancel`) aborts a request in flight.
 */
export function useSignOut() {
  return usePlainSignOut();
}

/**
 * "Sign out everywhere" (S-2.1.12): signs out here only after the server confirmed the revoke.
 * On failure it reports the error and leaves the session alone, so the user never believes their
 * other devices are signed out when they are not. `signOutHere` is the explicit fallback and
 * `cancel` aborts a request in flight (Escape or Cancel while pending).
 */
export function useSignOutEverywhere() {
  const client = useApiClient();
  const logoutAll = useLogoutAll();
  const plain = usePlainSignOut();
  const finish = useFinishSignOut();
  const [error, setError] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  const signOutEverywhere = async () => {
    setError(null);
    const abort = new AbortController();
    controller.current = abort;
    try {
      await logoutAll.mutateAsync(abort.signal);
    } catch {
      if (controller.current === abort) setError(SIGN_OUT_EVERYWHERE_FAILED); // not after cancel()
      return;
    }
    // Best effort: also revoke and clear a cookie that a concurrent refresh in another tab may
    // have rotated after logout-all ran (takes the refresh lock, 4 s timeout).
    try {
      await logoutCall(client);
    } catch {
      // The server already revoked every token; nothing more to do here.
    }
    finish();
  };
  const cancel = () => {
    const abort = controller.current;
    controller.current = null;
    abort?.abort();
  };
  return {
    signOutEverywhere,
    signOutHere: plain.signOut,
    cancel,
    pending: logoutAll.isPending || plain.signingOut,
    error,
  };
}
