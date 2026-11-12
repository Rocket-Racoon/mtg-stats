import { create } from 'zustand';

export type ToastKind = 'info' | 'error';
export type PauseReason = 'hover' | 'focus';

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
  /** Stops an info toast's auto-dismiss clock for a reason (hover or focus, WCAG 2.2.1). */
  pause: (id: number, reason: PauseReason) => void;
  /** Drops a pause reason; the clock restarts with the time left once no reason remains. */
  resume: (id: number, reason: PauseReason) => void;
}

let nextId = 1;

/** Info toasts dismiss themselves after this; error toasts stay until dismissed. */
export const INFO_TOAST_MS = 5000;
/** At most this many toasts are visible; the oldest info toast is dropped first. */
export const MAX_TOASTS = 3;

interface Timer {
  handle: ReturnType<typeof setTimeout> | null;
  remaining: number;
  startedAt: number;
  reasons: Set<PauseReason>;
}
const timers = new Map<number, Timer>();

function startTimer(id: number, remaining: number, dismiss: (id: number) => void): void {
  const handle = setTimeout(() => {
    dismiss(id);
  }, remaining);
  const reasons = timers.get(id)?.reasons ?? new Set<PauseReason>();
  timers.set(id, { handle, remaining, startedAt: Date.now(), reasons });
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
    let kept = get().toasts;
    while (kept.length > 0 && kept.length >= MAX_TOASTS) {
      // Oldest info toast first; an error is only dropped when every toast is an error.
      const victim = kept.find((t) => t.kind === 'info') ?? kept[0];
      if (!victim) break;
      clearTimer(victim.id);
      kept = kept.filter((t) => t !== victim);
    }
    set({ toasts: [...kept, { id, kind, message }] });
    if (kind === 'info') startTimer(id, INFO_TOAST_MS, get().dismiss);
    return id;
  },
  dismiss: (id) => {
    clearTimer(id);
    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
  },
  pause: (id, reason) => {
    const timer = timers.get(id);
    if (!timer) return;
    timer.reasons.add(reason);
    if (timer.handle === null) return;
    clearTimeout(timer.handle);
    timer.remaining = Math.max(0, timer.remaining - (Date.now() - timer.startedAt));
    timer.handle = null;
  },
  resume: (id, reason) => {
    const timer = timers.get(id);
    if (!timer) return;
    timer.reasons.delete(reason);
    if (timer.reasons.size > 0 || timer.handle !== null) return;
    startTimer(id, timer.remaining, get().dismiss);
  },
}));

/** Clears every toast and timer. For tests. */
export function resetToasts(): void {
  for (const id of [...timers.keys()]) clearTimer(id);
  useToastStore.setState({ toasts: [] });
}
