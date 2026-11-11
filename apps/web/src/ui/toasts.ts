import { create } from 'zustand';

export type ToastKind = 'info' | 'error';

export interface Toast {
  id: number;
  kind: ToastKind;
  message: string;
}

interface ToastState {
  toasts: Toast[];
  push: (kind: ToastKind, message: string) => number;
  dismiss: (id: number) => void;
}

let nextId = 1;

/** Info toasts dismiss themselves after this; error toasts stay until dismissed. */
export const INFO_TOAST_MS = 5000;

export const useToastStore = create<ToastState>()((set, get) => ({
  toasts: [],
  push: (kind, message) => {
    const id = nextId++;
    set((s) => ({ toasts: [...s.toasts, { id, kind, message }] }));
    if (kind === 'info') {
      setTimeout(() => {
        get().dismiss(id);
      }, INFO_TOAST_MS);
    }
    return id;
  },
  dismiss: (id) => {
    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
  },
}));
