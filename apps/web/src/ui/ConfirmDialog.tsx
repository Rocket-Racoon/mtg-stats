import {
  useEffect,
  useId,
  useRef,
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
} from 'react';
import { createPortal } from 'react-dom';

interface ConfirmDialogProps {
  title: string;
  children: ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  onConfirm: () => void;
  /**
   * Cancel button, Escape and the platform's close request all call this. It is allowed while
   * `pending`: the owner must then abort the request cleanly.
   */
  onCancel: () => void;
  /** While true the confirm button is `aria-disabled` and ignores clicks (focus is never lost). */
  pending?: boolean;
  /** Shown as an alert inside the dialog; focus moves to it when it appears. */
  error?: string | null;
  /** Extra buttons placed between Cancel and the confirm button (for example a fallback). */
  extraActions?: ReactNode;
  /**
   * Where focus goes when the dialog closes. Use it when the opener is gone or not focusable at
   * mount (a menu that closes in the same commit leaves `<body>` as the captured opener). Falls
   * back to the element focused at mount.
   */
  returnFocusRef?: RefObject<HTMLElement | null>;
}

/**
 * Modal confirmation on the native `<dialog>` opened with `showModal()`: a real modal (the page
 * behind is inert, Tab stays inside), Escape and the platform close request call `onCancel`, and
 * focus returns to the opener when it closes. Focus starts on Cancel, the safe choice. Buttons
 * use `aria-disabled` plus guards instead of `disabled`, so focus survives a pending request.
 * Rendered in a portal on `document.body` so a hidden ancestor cannot hide it.
 */
export function ConfirmDialog({
  title,
  children,
  confirmLabel,
  cancelLabel = 'Cancel',
  onConfirm,
  onCancel,
  pending = false,
  error = null,
  extraActions = null,
  returnFocusRef,
}: ConfirmDialogProps) {
  const titleId = useId();
  const descriptionId = useId();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const alertRef = useRef<HTMLParagraphElement>(null);
  const onCancelRef = useRef(onCancel);
  const returnFocusRefRef = useRef(returnFocusRef);
  useEffect(() => {
    onCancelRef.current = onCancel;
    returnFocusRefRef.current = returnFocusRef;
  });

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (!dialog.open) dialog.showModal();
    cancelRef.current?.focus();
    const onNativeCancel = (event: Event) => {
      // Keep the dialog open: the owner decides (it unmounts us, or aborts and stays).
      event.preventDefault();
      onCancelRef.current();
    };
    dialog.addEventListener('cancel', onNativeCancel);
    return () => {
      dialog.removeEventListener('cancel', onNativeCancel);
      // Close first: while it is modal the rest of the page is inert and cannot take focus.
      if (dialog.open) dialog.close();
      const target = returnFocusRefRef.current?.current;
      if (target?.isConnected) target.focus();
      else if (opener?.isConnected) opener.focus();
    };
  }, []);

  useEffect(() => {
    if (error) alertRef.current?.focus();
  }, [error]);

  const onKeyDown = (event: KeyboardEvent<HTMLDialogElement>) => {
    if (event.key !== 'Escape') return;
    event.preventDefault();
    event.stopPropagation();
    onCancel();
  };

  return createPortal(
    <dialog
      ref={dialogRef}
      aria-modal="true"
      aria-labelledby={titleId}
      aria-describedby={descriptionId}
      className="dialog"
      onKeyDown={onKeyDown}
    >
      <h2 id={titleId}>{title}</h2>
      <div id={descriptionId}>{children}</div>
      {error ? (
        <p ref={alertRef} role="alert" tabIndex={-1} className="form-error">
          {error}
        </p>
      ) : null}
      <div className="dialog-actions">
        <button type="button" ref={cancelRef} onClick={onCancel}>
          {cancelLabel}
        </button>
        {extraActions}
        <button
          type="button"
          aria-disabled={pending}
          onClick={() => {
            if (!pending) onConfirm();
          }}
        >
          {confirmLabel}
        </button>
      </div>
    </dialog>,
    document.body,
  );
}
