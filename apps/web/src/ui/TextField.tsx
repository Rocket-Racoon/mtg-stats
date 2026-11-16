import { forwardRef, type InputHTMLAttributes } from 'react';

export interface TextFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'id'> {
  id: string;
  label: string;
  /** Text shown next to the field and announced; also sets aria-invalid. */
  error?: string | null;
  /** Extra id to list in aria-describedby (for example a form-level error). */
  describedBy?: string | undefined;
}

/**
 * A labelled input with its error next to it. The error is a `role="alert"` element referenced
 * by `aria-describedby`, and the input gets `aria-invalid="true"` (S-2.1.6 AC7).
 */
export const TextField = forwardRef<HTMLInputElement, TextFieldProps>(function TextField(
  { id, label, error, describedBy, ...input },
  ref,
) {
  const errorId = `${id}-error`;
  const described = [error ? errorId : null, describedBy].filter(Boolean).join(' ');
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input
        {...input}
        ref={ref}
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={described || undefined}
      />
      {error ? (
        <p id={errorId} role="alert" className="field-error">
          {error}
        </p>
      ) : null}
    </div>
  );
});
