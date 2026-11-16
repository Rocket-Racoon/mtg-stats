import { useEffect, useState, type FormEvent } from 'react';
import { useNavigate, useParams } from 'react-router';
import { ApiError, useInvitePreview, useRegister } from '@mtg-stats/api-client';
import { useSession } from '../../session/session';
import { tooManyAttemptsMessage } from '../../session/rate-limit';
import { useSessionStore } from '../../session/store';
import {
  validateDisplayName,
  validateEmail,
  validatePassword,
  validateUsername,
} from '../../session/validation';
import { OpenInAppButton } from '../../ui/OpenInAppButton';
import { TextField } from '../../ui/TextField';
import { INVITE_INVALID_MESSAGE, SOMETHING_WENT_WRONG, fieldErrorsFromDetails } from './errors';

const FIELDS = ['username', 'display_name', 'password', 'email'] as const;
type Field = (typeof FIELDS)[number];
type Errors = Partial<Record<Field, string>>;

function isInviteGone(error: unknown): boolean {
  return (
    error instanceof ApiError && (error.status === 410 || error.code === 'invite_no_longer_valid')
  );
}

/** `/invite/:token`: who invited you, a registration form and "Open in app" (S-2.1.6). */
export function InvitePage() {
  const token = useParams()['*'] ?? '';
  const preview = useInvitePreview(token);
  const register = useRegister();
  const navigate = useNavigate();
  const { status } = useSession();
  const restoring = status === 'restoring';
  const [values, setValues] = useState<Record<Field, string>>({
    username: '',
    display_name: '',
    password: '',
    email: '',
  });
  const [errors, setErrors] = useState<Errors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [gone, setGone] = useState(false);

  useEffect(() => {
    document.title = 'Invite · MTG Stats';
  }, []);

  useEffect(() => {
    if (gone) document.getElementById('page-title')?.focus();
  }, [gone]);

  const set = (field: Field) => (value: string) => {
    setValues((current) => ({ ...current, [field]: value }));
  };

  const focusFirst = (found: Errors) => {
    const first = FIELDS.find((field) => found[field]);
    if (first) document.getElementById(first)?.focus();
  };

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    // Registering while the startup restore runs would let a late refresh undo the new session.
    if (register.isPending || restoring) return;
    const found: Errors = {};
    const checks: Record<Field, string | null> = {
      username: validateUsername(values.username),
      display_name: validateDisplayName(values.display_name),
      password: validatePassword(values.password),
      email: validateEmail(values.email),
    };
    for (const field of FIELDS) {
      const message = checks[field];
      if (message) found[field] = message;
    }
    setErrors(found);
    setFormError(null);
    if (Object.keys(found).length > 0) {
      focusFirst(found);
      return;
    }
    register.mutate(
      {
        invite_token: token,
        username: values.username,
        display_name: values.display_name.trim(),
        password: values.password,
        email: values.email.trim(),
      },
      {
        onSuccess: (tokens) => {
          useSessionStore.getState().signIn(tokens.access_token);
          void navigate('/', { replace: true });
        },
        onError: (error) => {
          if (isInviteGone(error)) {
            setGone(true);
            return;
          }
          const failed: Errors = {};
          let message: string | null = SOMETHING_WENT_WRONG;
          if (error instanceof ApiError) {
            if (error.status === 429) {
              message = tooManyAttemptsMessage(error.retryAfter);
            } else if (error.code === 'username_taken') {
              failed.username = error.message;
              message = null;
            } else if (error.status === 422) {
              const mapped = fieldErrorsFromDetails(error);
              for (const field of FIELDS) {
                const text = mapped[field];
                if (text) failed[field] = text;
              }
              if (Object.keys(failed).length > 0) message = null;
            }
          }
          setErrors(failed);
          setFormError(message);
          focusFirst(failed);
        },
      },
    );
  };

  const heading = (
    <h2 id="page-title" tabIndex={-1}>
      Invite
    </h2>
  );

  if (gone || (preview.isError && isInviteGone(preview.error))) {
    return (
      <section aria-labelledby="page-title" className="auth-page">
        {heading}
        <p role="alert">{INVITE_INVALID_MESSAGE}</p>
      </section>
    );
  }

  if (preview.isError) {
    return (
      <section aria-labelledby="page-title" className="auth-page">
        {heading}
        <p role="alert">{SOMETHING_WENT_WRONG}</p>
        <button
          type="button"
          disabled={preview.isFetching}
          onClick={() => {
            void preview.refetch();
          }}
        >
          {preview.isFetching ? 'Retrying…' : 'Retry'}
        </button>
        <OpenInAppButton kind="invite" value={token} />
      </section>
    );
  }

  if (!preview.data) {
    return (
      <section aria-labelledby="page-title" className="auth-page">
        {heading}
        <p role="status">Loading invite…</p>
        <OpenInAppButton kind="invite" value={token} />
      </section>
    );
  }

  return (
    <section aria-labelledby="page-title" className="auth-page">
      {heading}
      <p>{preview.data.inviter_display_name} invited you to join their playgroup.</p>
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
          value={values.username}
          error={errors.username ?? null}
          onChange={(event) => {
            set('username')(event.target.value);
          }}
        />
        <TextField
          id="display_name"
          name="display_name"
          label="Display name"
          autoComplete="nickname"
          value={values.display_name}
          error={errors.display_name ?? null}
          onChange={(event) => {
            set('display_name')(event.target.value);
          }}
        />
        <TextField
          id="password"
          name="password"
          label="Password"
          type="password"
          autoComplete="new-password"
          value={values.password}
          error={errors.password ?? null}
          onChange={(event) => {
            set('password')(event.target.value);
          }}
        />
        <TextField
          id="email"
          name="email"
          label="Email (optional)"
          type="email"
          autoComplete="email"
          value={values.email}
          error={errors.email ?? null}
          onChange={(event) => {
            set('email')(event.target.value);
          }}
        />
        {restoring ? <p role="status">Restoring your session…</p> : null}
        <button type="submit" disabled={register.isPending || restoring}>
          Create account
        </button>
      </form>
      <OpenInAppButton kind="invite" value={token} />
    </section>
  );
}
