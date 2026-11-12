import { create } from 'zustand';

export type SessionStatus = 'restoring' | 'signed_in' | 'signed_out';

interface SessionState {
  status: SessionStatus;
  setStatus: (status: SessionStatus) => void;
}

/**
 * Client-side session state. 'signed_out' is a placeholder until E-2 (S-2.1.6, S-2.1.12) restores
 * a real session; S-1.6.4 builds the route guard on top of this.
 */
export const useSessionStore = create<SessionState>()((set) => ({
  status: 'signed_out',
  setStatus: (status) => {
    set({ status });
  },
}));
