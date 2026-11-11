import { useToastStore } from './toasts';

/** Toast viewport. Errors use role="alert", info uses role="status" (both live regions). */
export function Toaster() {
  const toasts = useToastStore((s) => s.toasts);
  const dismiss = useToastStore((s) => s.dismiss);
  return (
    <div className="toaster">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`toast toast-${t.kind}`}
          role={t.kind === 'error' ? 'alert' : 'status'}
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
      ))}
    </div>
  );
}
