/**
 * Registry of resets for user-scoped client state (S-2.1.12). Anything that holds data belonging
 * to the signed-in user (a Zustand store, the TanStack Query cache, ...) registers a reset here, so
 * `clearLocalSession()` wipes it on sign-out, sign-out from another tab and session expiry. A new
 * user-scoped store must call `registerUserStateReset` at module load (or from an effect).
 */
type Reset = () => void;

const resets = new Set<Reset>();

/** Registers a reset to run on every local sign-out. Returns an unregister function. */
export function registerUserStateReset(reset: Reset): () => void {
  resets.add(reset);
  return () => {
    resets.delete(reset);
  };
}

/** Runs every registered reset. One failing reset never stops the others. */
export function resetUserState(): void {
  for (const reset of [...resets]) {
    try {
      reset();
    } catch {
      // Keep signing out: a broken reset must not leave the rest of the state behind.
    }
  }
}
