import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';

export type SessionStatus = 'signed-in' | 'signed-out';

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
  initialStatus = 'signed-out',
  children,
}: {
  initialStatus?: SessionStatus;
  children: ReactNode;
}) {
  const [status, setStatus] = useState<SessionStatus>(initialStatus);
  const signIn = useCallback(() => setStatus('signed-in'), []);
  const signOut = useCallback(() => setStatus('signed-out'), []);
  const value = useMemo(() => ({ status, signIn, signOut }), [status, signIn, signOut]);
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (!session) throw new Error('useSession must be used inside a SessionProvider');
  return session;
}
