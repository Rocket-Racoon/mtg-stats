import { useEffect, useRef } from 'react';
import { useToastStore, type Toast } from './toasts';

/**
 * Toast viewport. Two live regions stay in the DOM even when empty (a region added together with
 * its content is not reliably announced): role="status" for info, role="alert" for errors.
 */
export function Toaster() {
  const toasts = useToastStore((s) => s.toasts);
  const dismiss = useToastStore((s) => s.dismiss);
  const pause = useToastStore((s) => s.pause);
  const resume = useToastStore((s) => s.resume);
  const container = useRef<HTMLDivElement>(null);
  // The toast whose button holds focus, and the display order of the previous render.
  const focusedId = useRef<number | null>(null);
  const previousOrder = useRef<number[]>([]);

  const infos = toasts.filter((t) => t.kind === 'info');
  const errors = toasts.filter((t) => t.kind === 'error');

  // When the toast that held focus disappears (dismissed, timed out or dropped by the cap), move
  // focus to the next toast's Dismiss button, else the previous one, else #main. Focus that was
  // never inside the toaster is left alone.
  useEffect(() => {
    const order = [
      ...toasts.filter((t) => t.kind === 'info'),
      ...toasts.filter((t) => t.kind === 'error'),
    ].map((t) => t.id);
    const lost = focusedId.current;
    if (lost !== null && !order.includes(lost)) {
      focusedId.current = null;
      const stillFocused = container.current?.contains(document.activeElement) ?? false;
      if (!stillFocused) {
        const was = previousOrder.current;
        const index = was.indexOf(lost);
        const next =
          was.slice(index + 1).find((id) => order.includes(id)) ??
          was
            .slice(0, Math.max(0, index))
            .reverse()
            .find((id) => order.includes(id));
        if (next === undefined) {
          document.getElementById('main')?.focus({ preventScroll: true });
        } else {
          container.current
            ?.querySelector<HTMLElement>(`[data-toast-id="${String(next)}"] button`)
            ?.focus();
        }
      }
    }
    previousOrder.current = order;
  }, [toasts]);

  const item = (t: Toast) => (
    <div
      key={t.id}
      data-toast-id={t.id}
      className={`toast toast-${t.kind}`}
      onMouseEnter={() => {
        pause(t.id, 'hover');
      }}
      onMouseLeave={() => {
        resume(t.id, 'hover');
      }}
      onFocus={() => {
        focusedId.current = t.id;
        pause(t.id, 'focus');
      }}
      onBlur={(event) => {
        // A removed element may blur on its way out; only a still-mounted toast means the user
        // moved focus away deliberately.
        if (event.currentTarget.isConnected) focusedId.current = null;
        resume(t.id, 'focus');
      }}
    >
      <span>{t.message}</span>
      <button
        type="button"
        aria-label={`Dismiss notification: ${t.message}`}
        onClick={() => {
          dismiss(t.id);
        }}
      >
        Dismiss
      </button>
    </div>
  );

  return (
    <div ref={container} role="region" aria-label="Notifications" className="toaster">
      <div role="status" aria-live="polite" className="toast-list">
        {infos.map(item)}
      </div>
      <div role="alert" aria-live="assertive" className="toast-list">
        {errors.map(item)}
      </div>
    </div>
  );
}
