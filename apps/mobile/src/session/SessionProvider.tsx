import { useState, type ReactNode } from 'react';
import { useSessionStore, type SessionStatus } from './store';

export type { SessionStatus } from './store';

/**
 * Test seam only: sets the store's initial status before the first render of `children`. The app
 * itself needs no provider; the Zustand store is the session.
 */
export function SessionProvider({
  initialStatus,
  children,
}: {
  initialStatus?: SessionStatus;
  children: ReactNode;
}) {
  useState(() => {
    if (initialStatus) useSessionStore.setState({ status: initialStatus, pendingHref: null });
    return null;
  });
  return <>{children}</>;
}

export function useSession() {
  const status = useSessionStore((s) => s.status);
  const signIn = useSessionStore((s) => s.signIn);
  const signOut = useSessionStore((s) => s.signOut);
  return { status, signIn, signOut };
}
