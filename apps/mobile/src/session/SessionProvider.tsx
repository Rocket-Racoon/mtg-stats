import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';

/** `restoring` is the cold-start window while the stored session is read; it never flashes sign-in. */
export type SessionStatus = 'restoring' | 'signed_in' | 'signed_out';

/**
 * The session interface the route guards depend on. E-2's secure session store implements it
 * (tokens stay in that store on expo-secure-store); nothing here touches a token.
 */
export type Session = {
  status: SessionStatus;
  signIn: () => void;
  signOut: () => void;
};

const SessionContext = createContext<Session | null>(null);

/**
 * In-memory session placeholder. Real tokens and the sign-in flow arrive with E-2; they replace
 * this provider but keep the `useSession()` shape so the route guards need no change.
 */
export function SessionProvider({
  initialStatus = 'signed_out',
  children,
}: {
  initialStatus?: SessionStatus;
  children: ReactNode;
}) {
  const [status, setStatus] = useState<SessionStatus>(initialStatus);
  const signIn = useCallback(() => setStatus('signed_in'), []);
  const signOut = useCallback(() => setStatus('signed_out'), []);
  const value = useMemo(() => ({ status, signIn, signOut }), [status, signIn, signOut]);
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (!session) throw new Error('useSession must be used inside a SessionProvider');
  return session;
}
