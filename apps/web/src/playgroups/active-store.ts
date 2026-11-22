import { create } from 'zustand';
import { createJSONStorage, persist, type StateStorage } from 'zustand/middleware';
import { registerUserStateReset } from '../session/user-state';

/** localStorage key of the remembered active playgroup (S-2.4.5). */
export const ACTIVE_PLAYGROUP_ITEM_NAME = 'mtg-stats-active-playgroup';

/** localStorage behind try/catch: blocked or full storage means "not remembered", never a crash. */
const safeStorage: StateStorage = {
  getItem: (name) => {
    try {
      return localStorage.getItem(name);
    } catch {
      return null;
    }
  },
  setItem: (name, value) => {
    try {
      localStorage.setItem(name, value);
    } catch {
      // Works without storage.
    }
  },
  removeItem: (name) => {
    try {
      localStorage.removeItem(name);
    } catch {
      // Works without storage.
    }
  },
};

interface ActivePlaygroupState {
  /** The id the user picked on this device, or null. Reconciled against the API list. */
  activeId: string | null;
  setActive: (playgroupId: string | null) => void;
}

/**
 * The active playgroup, remembered per device. It is user-scoped client state: it registers a
 * reset with the sign-out registry (session/user-state.ts), so sign-out, sign-out from another tab
 * and session expiry clear it and the next account on this browser never inherits the choice.
 */
export const useActivePlaygroupStore = create<ActivePlaygroupState>()(
  persist(
    (set) => ({
      activeId: null,
      setActive: (playgroupId) => {
        set({ activeId: playgroupId });
      },
    }),
    {
      name: ACTIVE_PLAYGROUP_ITEM_NAME,
      version: 1,
      storage: createJSONStorage(() => safeStorage),
      partialize: (state) => ({ activeId: state.activeId }),
      // Never trust stored JSON: anything but a non-empty string is "no choice".
      merge: (persisted, current) => {
        const stored = (persisted as { activeId?: unknown } | null | undefined)?.activeId;
        return {
          ...current,
          activeId: typeof stored === 'string' && stored !== '' ? stored : null,
        };
      },
    },
  ),
);

registerUserStateReset(() => {
  useActivePlaygroupStore.setState({ activeId: null });
  void useActivePlaygroupStore.persist.clearStorage();
});

/**
 * Reconciles the remembered id against the caller's playgroups, in the API order: the stored id
 * when it is still one of them, else the first playgroup, else null (no playgroups, no active).
 */
export function reconcileActive(
  storedId: string | null,
  playgroups: readonly { id: string }[],
): string | null {
  if (storedId !== null && playgroups.some((p) => p.id === storedId)) return storedId;
  return playgroups[0]?.id ?? null;
}
