import { create } from 'zustand';
import { clearLogoutPending } from './pending-logout';

export type SessionStatus = 'restoring' | 'signed_in' | 'signed_out';

/** Why the session ended: the user chose it (or another tab did), or the refresh was rejected. */
export type SignOutReason = 'user' | 'expired';

interface SessionState {
  status: SessionStatus;
  /** The access token (decision B3: in memory only; never persisted, no Web Storage). */
  accessToken: string | null;
  /** Why the last sign-out happened; null when signed in or never signed in (S-2.1.12). */
  signOutReason: SignOutReason | null;
  setStatus: (status: SessionStatus) => void;
  /** Stores the access token and marks the session signed in. */
  signIn: (accessToken: string) => void;
  /** Replaces the token without touching the status (a refresh). */
  setAccessToken: (accessToken: string | null) => void;
  /** Drops the token and marks the session signed out. */
  signOut: (reason?: SignOutReason) => void;
}

/**
 * Client-side session state (S-2.1.6). It starts as 'restoring': `restoreSession()` (called once
 * from main.tsx) resolves it through the cookie refresh before protected content renders. There
 * is deliberately no `persist` middleware: the refresh token is only the HttpOnly cookie.
 */
export const useSessionStore = create<SessionState>()((set) => ({
  status: 'restoring',
  accessToken: null,
  signOutReason: null,
  setStatus: (status) => {
    set({ status });
  },
  signIn: (accessToken) => {
    // Every successful sign-in (any page) ends a failed earlier sign-out: the new cookie is a new
    // session, and a leftover flag would force the next load to log it out.
    clearLogoutPending();
    set({ accessToken, status: 'signed_in', signOutReason: null });
  },
  setAccessToken: (accessToken) => {
    set({ accessToken });
  },
  signOut: (reason) => {
    set({ accessToken: null, status: 'signed_out', signOutReason: reason ?? null });
  },
}));
