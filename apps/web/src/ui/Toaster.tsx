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
  // Where focus goes once the dismissed toast is gone: a toast id, 'main', or nothing.
  const focusAfter = useRef<number | 'main' | null>(null);

  const infos = toasts.filter((t) => t.kind === 'info');
  const errors = toasts.filter((t) => t.kind === 'error');

  useEffect(() => {
    const target = focusAfter.current;
    if (target === null) return;
    focusAfter.current = null;
    if (target === 'main') {
      document.getElementById('main')?.focus({ preventScroll: true });
    } else {
      container.current
        ?.querySelector<HTMLElement>(`[data-toast-id="${String(target)}"] button`)
        ?.focus();
    }
  }, [toasts]);

  const onDismiss = (id: number) => {
    // Only move focus when it is inside the toaster; never steal it otherwise.
    if (container.current?.contains(document.activeElement)) {
      const order = [...infos, ...errors];
      const index = order.findIndex((t) => t.id === id);
      const next = order[index + 1] ?? order[index - 1];
      focusAfter.current = next ? next.id : 'main';
    }
    dismiss(id);
  };

  const item = (t: Toast) => (
    <div
      key={t.id}
      data-toast-id={t.id}
      className={`toast toast-${t.kind}`}
      onMouseEnter={() => {
        pause(t.id);
      }}
      onMouseLeave={() => {
        resume(t.id);
      }}
      onFocus={() => {
        pause(t.id);
      }}
      onBlur={() => {
        resume(t.id);
      }}
    >
      <span>{t.message}</span>
      <button
        type="button"
        aria-label={`Dismiss notification: ${t.message}`}
        onClick={() => {
          onDismiss(t.id);
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
