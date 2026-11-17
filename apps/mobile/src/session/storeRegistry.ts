type Reset = () => void;

const resets = new Set<Reset>();

/**
 * Registers a reset for a user-scoped store (S-2.1.12). Every Zustand store or cache that holds
 * data about the signed-in user registers here, so `signOut` clears them all in one place. Returns
 * an unregister function. The TanStack query cache registers itself in `AppProviders`.
 */
export function registerStoreReset(reset: Reset): () => void {
  resets.add(reset);
  return () => {
    resets.delete(reset);
  };
}

/** Runs every registered reset. One failing reset never stops the others. */
export function resetUserStores(): void {
  for (const reset of [...resets]) {
    try {
      reset();
    } catch {
      // Keep clearing the rest: signing out must always finish.
    }
  }
}
