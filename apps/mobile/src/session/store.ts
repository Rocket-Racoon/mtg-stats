import { create } from 'zustand';
import { deleteTokens, writeTokens } from './secureStorage';

export type SessionStatus = 'restoring' | 'signed-in' | 'signed-out';

export interface SessionTokens {
  accessToken: string;
  refreshToken: string;
}

/** An in-app path only: starts with one `/`, no `//` or backslash (never an external URL). */
export function isInAppPath(value: string): boolean {
  return value.startsWith('/') && !value.startsWith('//') && !value.includes('\\');
}

interface SessionState {
  status: SessionStatus;
  accessToken: string | null;
  refreshToken: string | null;
  /** The protected link a signed-out user opened; memory only, used once after sign-in. */
  pendingHref: string | null;
  /** A one-off message for the sign-in screen (S-2.1.12); cleared on sign-in. */
  notice: 'session-expired' | null;
  setNotice: (notice: 'session-expired' | null) => void;
  setStatus: (status: SessionStatus) => void;
  /** Memory, then the secure-store write, then `signed-in`. */
  signIn: (tokens: SessionTokens) => Promise<void>;
  /** Replaces the tokens (a refresh) without touching the status. */
  setTokens: (tokens: { accessToken: string; refreshToken?: string }) => Promise<void>;
  /** Nulls both tokens, deletes both secure-store keys, marks signed out. Idempotent. */
  signOut: () => Promise<void>;
  /** Ignores anything that is not an in-app path. */
  setPendingHref: (href: string | null) => void;
}

/**
 * Client-side session state (S-2.1.7), the mobile twin of web's store. It starts as 'restoring';
 * `restoreSession()` resolves it at launch. The refresh token lives in memory and in
 * expo-secure-store only, never in AsyncStorage.
 */
export const useSessionStore = create<SessionState>()((set, get) => ({
  status: 'restoring',
  accessToken: null,
  refreshToken: null,
  pendingHref: null,
  notice: null,
  setNotice: (notice) => {
    set({ notice });
  },
  setStatus: (status) => {
    set({ status });
  },
  signIn: async ({ accessToken, refreshToken }) => {
    set({ accessToken, refreshToken, notice: null });
    await writeTokens({ accessToken, refreshToken });
    set({ status: 'signed-in' });
  },
  setTokens: async ({ accessToken, refreshToken }) => {
    // A refresh that finishes after sign-out must not bring the tokens back. The launch restore
    // sets tokens while 'restoring', so it is unaffected.
    if (get().status === 'signed-out') return;
    set(refreshToken === undefined ? { accessToken } : { accessToken, refreshToken });
    await writeTokens({ accessToken, refreshToken });
  },
  signOut: async () => {
    set({ accessToken: null, refreshToken: null, status: 'signed-out' });
    await deleteTokens();
  },
  setPendingHref: (href) => {
    if (href !== null && !isInAppPath(href)) return;
    if (get().pendingHref !== href) set({ pendingHref: href });
  },
}));
