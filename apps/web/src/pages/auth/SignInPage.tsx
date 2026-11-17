import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Navigate, useSearchParams } from 'react-router';
import { useSignIn } from '@mtg-stats/api-client';
import { safeNext } from '../../session/next';
import { useSession } from '../../session/session';
import { useSessionStore } from '../../session/store';
import { requireValue } from '../../session/validation';
import { TextField } from '../../ui/TextField';
import { formErrorMessage } from './errors';

interface Errors {
  username?: string;
  password?: string;
}

/** `/sign-in`: username and password (decision C1). Honours `?next=` through `safeNext`. */
export function SignInPage() {
  const { status } = useSession();
  const [params] = useSearchParams();
  const next = safeNext(params.get('next'));
  const signInMutation = useSignIn();
  const signOutReason = useSessionStore((s) => s.signOutReason);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [errors, setErrors] = useState<Errors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const usernameRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    document.title = 'Sign in · MTG Stats';
  }, []);

  // After the user signed out, announce the page: focus its heading (S-2.1.12).
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (status === 'signed_out' && signOutReason === 'user') headingRef.current?.focus();
  }, [status, signOutReason]);

  // A signed-in visitor has nothing to do here. Our own successful sign-in lands here too.
  if (status === 'signed_in') return <Navigate replace to={next} />;

  if (status === 'restoring') {
    return (
      <section aria-labelledby="page-title">
        <h2 id="page-title">Sign in</h2>
        <p role="status">Loading…</p>
      </section>
    );
  }

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (signInMutation.isPending) return;
    const found: Errors = {};
    const usernameError = requireValue('Username', username);
    const passwordError = requireValue('Password', password);
    if (usernameError) found.username = usernameError;
    if (passwordError) found.password = passwordError;
    setErrors(found);
    setFormError(null);
    if (found.username) {
      usernameRef.current?.focus();
      return;
    }
    if (found.password) {
      passwordRef.current?.focus();
      return;
    }
    signInMutation.mutate(
      { username, password },
      {
        onSuccess: (tokens) => {
          useSessionStore.getState().signIn(tokens.access_token);
        },
        onError: (error) => {
          setFormError(formErrorMessage(error));
          setPassword('');
          passwordRef.current?.focus();
        },
      },
    );
  };

  return (
    <section aria-labelledby="page-title" className="auth-page">
      <h2 id="page-title" ref={headingRef} tabIndex={-1}>
        Sign in
      </h2>
      {signOutReason === 'expired' ? (
        <p role="alert" className="form-error">
          Your session has expired
        </p>
      ) : null}
      <form onSubmit={onSubmit} noValidate>
        {formError ? (
          <p id="form-error" role="alert" className="form-error">
            {formError}
          </p>
        ) : null}
        <TextField
          id="username"
          name="username"
          label="Username"
          autoComplete="username"
          autoCapitalize="none"
          spellCheck={false}
          value={username}
          error={errors.username ?? null}
          onChange={(event) => {
            setUsername(event.target.value);
          }}
          ref={usernameRef}
        />
        <TextField
          id="password"
          name="password"
          label="Password"
          type="password"
          autoComplete="current-password"
          value={password}
          error={errors.password ?? null}
          describedBy={formError ? 'form-error' : undefined}
          onChange={(event) => {
            setPassword(event.target.value);
          }}
          ref={passwordRef}
        />
        <button type="submit" disabled={signInMutation.isPending}>
          Sign in
        </button>
      </form>
    </section>
  );
}
