import { createContext, useContext } from 'react';
import { useSessionStore, type SessionStatus } from './store';

/**
 * The session interface the route guard reads (S-1.6.4). It exposes only "restoring, signed in or
 * signed out"; the guard never sees tokens, users or API calls.
 *
 * E-2's real session store (S-2.1.6, S-2.1.12) must implement this: `useSession()` has to keep
 * returning `{ status }` with these three values, whatever the store holds behind it. Today the
 * default implementation reads `useSessionStore`.
 */
export interface Session {
  status: SessionStatus;
}

/** Override for tests (or a future non-store source). `null` means "use the store". */
export const SessionContext = createContext<Session | null>(null);

export function useSession(): Session {
  const override = useContext(SessionContext);
  const status = useSessionStore((s) => s.status);
  return override ?? { status };
}
