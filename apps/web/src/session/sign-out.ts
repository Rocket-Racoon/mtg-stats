import { useSessionStore, type SignOutReason } from './store';
import { resetUserState } from './user-state';

/** BroadcastChannel carrying sign-out between tabs (S-2.1.12). Carries no data but the event. */
export const SIGN_OUT_CHANNEL = 'mtgstats-session';

const SIGNED_OUT_MESSAGE = { type: 'signed_out' } as const;

let channel: BroadcastChannel | null = null;

/** One shared instance: a channel never delivers its own posts back to itself, so no echo. */
function getChannel(): BroadcastChannel | null {
  if (typeof BroadcastChannel === 'undefined') return null;
  channel ??= new BroadcastChannel(SIGN_OUT_CHANNEL);
  return channel;
}

/**
 * The one place that clears everything: every registered user-scoped reset (query cache, stores),
 * the in-memory access token, and the session status. Does not call the API and does not
 * broadcast. Safe to call twice.
 */
export function clearLocalSession(reason: SignOutReason = 'user'): void {
  resetUserState();
  useSessionStore.getState().signOut(reason);
}

/** Tells the other tabs to sign out. Tolerates a missing BroadcastChannel. */
export function broadcastSignOut(): void {
  try {
    getChannel()?.postMessage(SIGNED_OUT_MESSAGE);
  } catch {
    // Best effort: other tabs find out on their next refresh at the latest.
  }
}

/** The user signed out here: clear this tab, then tell the others. */
export function completeSignOut(): void {
  clearLocalSession('user');
  broadcastSignOut();
}

/**
 * Handler for the API client's `onSessionExpired` (S-1.5.3). While signed in it clears everything
 * and records the "expired" reason, which `RequireSession` turns into /sign-in?next=<path> and the
 * sign-in page into "Your session has expired". During the startup restore (status 'restoring')
 * it is a plain sign-out: a visitor with no session was never
 * "expired". Not broadcast: every tab finds out through its own refresh.
 */
export function handleSessionExpired(): void {
  const { status } = useSessionStore.getState();
  if (status === 'signed_out') return; // already out: keep the reason that is recorded
  if (status === 'signed_in') clearLocalSession('expired');
  else useSessionStore.getState().signOut();
}

/**
 * Listens for sign-outs from other tabs and signs this one out without rebroadcasting. Call once
 * from main.tsx. Returns a stop function. A no-op without BroadcastChannel.
 */
export function startSignOutSync(): () => void {
  const bc = getChannel();
  if (!bc) return () => undefined;
  const onMessage = (event: MessageEvent<unknown>) => {
    const data = event.data as { type?: unknown } | null;
    if (data?.type !== 'signed_out') return;
    // A tab still 'restoring' has no session to drop (and no deep link to lose): ignore.
    if (useSessionStore.getState().status !== 'signed_in') return;
    clearLocalSession('user');
  };
  bc.addEventListener('message', onMessage);
  return () => {
    bc.removeEventListener('message', onMessage);
  };
}
