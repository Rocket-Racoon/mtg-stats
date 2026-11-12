import type { ReactNode } from 'react';
import { SessionContext, type Session } from './session';

/** Supplies a fixed session to the tree, instead of the store. Used by tests with a fake session. */
export function SessionProvider({ session, children }: { session: Session; children: ReactNode }) {
  return <SessionContext.Provider value={session}>{children}</SessionContext.Provider>;
}
