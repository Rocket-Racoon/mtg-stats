import { useEffect, useState, type FormEvent } from 'react';
import { Link } from 'react-router';
import { ApiError, useJoinWithInvite } from '@mtg-stats/api-client';
import { errorMessage } from '../errors/message';
import { useActivePlaygroupStore } from '../playgroups/active-store';
import { TextField } from '../ui/TextField';
import { INVITE_INVALID_MESSAGE } from './auth/errors';

export const INVITE_EMPTY_MESSAGE = 'Paste the invite link or code you were given.';
export const INVITE_UNREADABLE_MESSAGE = 'That does not look like an invite link or code.';

/**
 * The invite token from what a person pastes: an invite link (`https://host/invite/<token>` or
 * `mtgstats://invite/<token>`) or the bare token. Returns null when it is empty or cannot be a
 * token (other paths, spaces, more segments).
 */
export function extractInviteToken(raw: string): string | null {
  const text = raw.trim();
  if (text === '') return null;
  // The last /invite/<token> path segment of whatever was pasted, wherever it sits in the URL.
  // Only the token is used; the pasted URL is never opened.
  const link = /(?:^|\/)invite\/([^/\s]+)\/?$/i.exec(text.split(/[?#]/, 1)[0] ?? '');
  const candidate = link?.[1] ?? text;
  if (/[\s/?#]/.test(candidate)) return null;
  try {
    return decodeURIComponent(candidate);
  } catch {
    return null;
  }
}

/**
 * `/playgroups/join`: "I have an invite" (S-2.4.5). The smallest entry point that works with the
 * existing API: paste the invite link or code and join with `POST /invites/join`. The invite UI
 * proper (S-2.4.2) replaces or extends this. Joining makes that playgroup the active one.
 */
export function JoinWithInvitePage() {
  const join = useJoinWithInvite();
  const setActive = useActivePlaygroupStore((s) => s.setActive);
  const [value, setValue] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [joined, setJoined] = useState<{ id: string; name: string; already: boolean } | null>(null);

  useEffect(() => {
    document.title = 'Join a playgroup · MTG Stats';
  }, []);

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (join.isPending) return;
    setJoined(null);
    const token = extractInviteToken(value);
    if (token === null) {
      setError(value.trim() === '' ? INVITE_EMPTY_MESSAGE : INVITE_UNREADABLE_MESSAGE);
      document.getElementById('invite-value')?.focus();
      return;
    }
    setError(null);
    join.mutate(token, {
      onSuccess: ({ already_member, playgroup }) => {
        setActive(playgroup.id);
        setValue('');
        setJoined({ id: playgroup.id, name: playgroup.name, already: already_member });
      },
      onError: (failure) => {
        setError(
          failure instanceof ApiError && failure.code === 'invite_no_longer_valid'
            ? INVITE_INVALID_MESSAGE
            : errorMessage(failure),
        );
        document.getElementById('invite-value')?.focus();
      },
    });
  };

  return (
    <section aria-labelledby="page-title" className="auth-page">
      <h2 id="page-title">Join a playgroup</h2>
      <form onSubmit={onSubmit} noValidate>
        <TextField
          id="invite-value"
          name="invite"
          label="Invite link or code"
          autoComplete="off"
          autoCapitalize="none"
          spellCheck={false}
          value={value}
          error={error}
          onChange={(event) => {
            setValue(event.target.value);
          }}
        />
        <button type="submit" disabled={join.isPending}>
          {join.isPending ? 'Joining…' : 'Join playgroup'}
        </button>
      </form>
      <p role="status">
        {joined
          ? joined.already
            ? `You're already in ${joined.name}.`
            : `You joined ${joined.name}.`
          : ''}
      </p>
      {joined ? (
        <p>
          <Link to={`/playgroups/${joined.id}`}>Go to {joined.name}</Link>
        </p>
      ) : null}
    </section>
  );
}
