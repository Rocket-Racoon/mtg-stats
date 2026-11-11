import { create } from 'zustand';

export type ToastKind = 'info' | 'error';

export interface Toast {
  id: number;
  kind: ToastKind;
  message: string;
}

interface ToastState {
  toasts: Toast[];
  /** Adds a toast and returns its id; an identical live toast (same kind and message) is reused. */
  push: (kind: ToastKind, message: string) => number;
  dismiss: (id: number) => void;
  /** Stops an info toast's auto-dismiss clock (hover or focus, WCAG 2.2.1). */
  pause: (id: number) => void;
  /** Restarts the clock with the time that was left. */
  resume: (id: number) => void;
}

let nextId = 1;

/** Info toasts dismiss themselves after this; error toasts stay until dismissed. */
export const INFO_TOAST_MS = 5000;
/** At most this many toasts are visible; the oldest is dropped first. */
export const MAX_TOASTS = 3;

interface Timer {
  handle: ReturnType<typeof setTimeout> | null;
  remaining: number;
  startedAt: number;
}
const timers = new Map<number, Timer>();

function startTimer(id: number, remaining: number, dismiss: (id: number) => void): void {
  const handle = setTimeout(() => {
    dismiss(id);
  }, remaining);
  timers.set(id, { handle, remaining, startedAt: Date.now() });
}

function clearTimer(id: number): void {
  const timer = timers.get(id);
  if (timer?.handle != null) clearTimeout(timer.handle);
  timers.delete(id);
}

export const useToastStore = create<ToastState>()((set, get) => ({
  toasts: [],
  push: (kind, message) => {
    const existing = get().toasts.find((t) => t.kind === kind && t.message === message);
    if (existing) return existing.id;
    const id = nextId++;
    const kept = get().toasts.slice(-(MAX_TOASTS - 1));
    for (const dropped of get().toasts.filter((t) => !kept.includes(t))) clearTimer(dropped.id);
    set({ toasts: [...kept, { id, kind, message }] });
    if (kind === 'info') startTimer(id, INFO_TOAST_MS, get().dismiss);
    return id;
  },
  dismiss: (id) => {
    clearTimer(id);
    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
  },
  pause: (id) => {
    const timer = timers.get(id);
    if (timer?.handle == null) return;
    clearTimeout(timer.handle);
    timers.set(id, {
      handle: null,
      remaining: Math.max(0, timer.remaining - (Date.now() - timer.startedAt)),
      startedAt: 0,
    });
  },
  resume: (id) => {
    const timer = timers.get(id);
    if (timer === undefined || timer.handle !== null) return;
    startTimer(id, timer.remaining, get().dismiss);
  },
}));
