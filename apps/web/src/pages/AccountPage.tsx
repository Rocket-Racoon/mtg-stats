import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ApiError, useChangePassword } from '@mtg-stats/api-client';
import { tooManyAttemptsMessage } from '../session/rate-limit';
import { useSessionStore } from '../session/store';
import { requireValue, validatePassword } from '../session/validation';
import { TextField } from '../ui/TextField';
import { SOMETHING_WENT_WRONG, fieldErrorsFromDetails } from './auth/errors';

interface Errors {
  current?: string;
  next?: string;
  confirm?: string;
}

/** `/account`: account settings. For now only the change-password form (S-2.1.8). */
export function AccountPage() {
  const mutation = useChangePassword();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [errors, setErrors] = useState<Errors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [status, setStatus] = useState('');
  const currentRef = useRef<HTMLInputElement>(null);
  const nextRef = useRef<HTMLInputElement>(null);
  const confirmRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    document.title = 'Account · MTG Stats';
  }, []);

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (mutation.isPending) return;
    const found: Errors = {};
    const currentError = requireValue('Current password', current);
    const nextError = requireValue('New password', next) ?? validatePassword(next);
    const confirmError =
      requireValue('Confirm new password', confirm) ??
      (confirm !== next ? "Passwords don't match." : null);
    if (currentError) found.current = currentError;
    if (nextError) found.next = nextError;
    if (confirmError) found.confirm = confirmError;
    setErrors(found);
    setFormError(null);
    setStatus('');
    if (found.current) {
      currentRef.current?.focus();
      return;
    }
    if (found.next) {
      nextRef.current?.focus();
      return;
    }
    if (found.confirm) {
      confirmRef.current?.focus();
      return;
    }
    mutation.mutate(
      { current_password: current, new_password: next },
      {
        onSuccess: (tokens) => {
          // The refresh cookie was rotated by the response; only the access token is ours to store.
          useSessionStore.getState().signIn(tokens.access_token);
          setCurrent('');
          setNext('');
          setConfirm('');
          setStatus('Password changed.');
        },
        onError: (error) => {
          if (error instanceof ApiError && error.code === 'invalid_current_password') {
            const mapped = fieldErrorsFromDetails(error);
            setErrors({ current: mapped.current_password ?? error.message });
            setCurrent('');
            currentRef.current?.focus();
          } else if (error instanceof ApiError && error.status === 422) {
            const mapped = fieldErrorsFromDetails(error);
            const fieldErrors: Errors = {};
            if (mapped.current_password) fieldErrors.current = mapped.current_password;
            if (mapped.new_password) fieldErrors.next = mapped.new_password;
            if (Object.keys(fieldErrors).length > 0) {
              setErrors(fieldErrors);
              if (fieldErrors.current) currentRef.current?.focus();
              else nextRef.current?.focus();
            } else setFormError(SOMETHING_WENT_WRONG);
          } else if (error instanceof ApiError && error.status === 429) {
            setFormError(tooManyAttemptsMessage(error.retryAfter));
          } else {
            setFormError(SOMETHING_WENT_WRONG);
          }
        },
      },
    );
  };

  return (
    <section aria-labelledby="page-title">
      <h2 id="page-title">Account</h2>
      <section aria-labelledby="change-password-title" className="auth-page">
        <h3 id="change-password-title">Change password</h3>
        <form onSubmit={onSubmit} noValidate>
          {formError ? (
            <p id="form-error" role="alert" className="form-error">
              {formError}
            </p>
          ) : null}
          <TextField
            id="current-password"
            name="current_password"
            label="Current password"
            type="password"
            autoComplete="current-password"
            value={current}
            error={errors.current ?? null}
            describedBy={formError ? 'form-error' : undefined}
            onChange={(event) => {
              setCurrent(event.target.value);
            }}
            ref={currentRef}
          />
          <TextField
            id="new-password"
            name="new_password"
            label="New password"
            type="password"
            autoComplete="new-password"
            value={next}
            error={errors.next ?? null}
            onChange={(event) => {
              setNext(event.target.value);
            }}
            ref={nextRef}
          />
          <TextField
            id="confirm-password"
            name="confirm_password"
            label="Confirm new password"
            type="password"
            autoComplete="new-password"
            value={confirm}
            error={errors.confirm ?? null}
            onChange={(event) => {
              setConfirm(event.target.value);
            }}
            ref={confirmRef}
          />
          <button type="submit" disabled={mutation.isPending}>
            {mutation.isPending ? 'Changing password…' : 'Change password'}
          </button>
          <p role="status">{status}</p>
        </form>
      </section>
    </section>
  );
}
