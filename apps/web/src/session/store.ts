import { create } from 'zustand';

export type SessionStatus = 'restoring' | 'signed_in' | 'signed_out';

interface SessionState {
  status: SessionStatus;
  /** The access token (decision B3: in memory only; never persisted, no Web Storage). */
  accessToken: string | null;
  setStatus: (status: SessionStatus) => void;
  /** Stores the access token and marks the session signed in. */
  signIn: (accessToken: string) => void;
  /** Replaces the token without touching the status (a refresh). */
  setAccessToken: (accessToken: string | null) => void;
  /** Drops the token and marks the session signed out. */
  signOut: () => void;
}

/**
 * Client-side session state (S-2.1.6). It starts as 'restoring': `restoreSession()` (called once
 * from main.tsx) resolves it through the cookie refresh before protected content renders. There
 * is deliberately no `persist` middleware: the refresh token is only the HttpOnly cookie.
 */
export const useSessionStore = create<SessionState>()((set) => ({
  status: 'restoring',
  accessToken: null,
  setStatus: (status) => {
    set({ status });
  },
  signIn: (accessToken) => {
    set({ accessToken, status: 'signed_in' });
  },
  setAccessToken: (accessToken) => {
    set({ accessToken });
  },
  signOut: () => {
    set({ accessToken: null, status: 'signed_out' });
  },
}));
