import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ApiError, useCreatePlaygroup, usePlaygroups } from '@mtg-stats/api-client';
import { errorMessage } from '../errors/message';
import { TextField } from '../ui/TextField';
import { useToastStore } from '../ui/toasts';
import { fieldErrorsFromDetails } from './auth/errors';

const NAME_MIN = 3;
const NAME_MAX = 50;
const DESCRIPTION_MAX = 500;

/** Length in code points, matching the API's Python `len()`. */
const count = (value: string) => [...value].length;

interface Errors {
  name?: string;
  description?: string;
}

/** Mirrors the API's bounds: name 3 to 50 after trimming, description up to 500. */
export function validatePlaygroup(name: string, description: string): Errors {
  const errors: Errors = {};
  const length = count(name.trim());
  if (length < NAME_MIN || length > NAME_MAX) {
    errors.name = `Name must be ${String(NAME_MIN)} to ${String(NAME_MAX)} characters.`;
  }
  if (count(description) > DESCRIPTION_MAX) {
    errors.description = `Description must be at most ${String(DESCRIPTION_MAX)} characters.`;
  }
  return errors;
}

/** `/playgroups`: the caller's playgroups and the create form (S-2.4.1). */
export function PlaygroupsPage() {
  const mutation = useCreatePlaygroup();
  const list = usePlaygroups();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [errors, setErrors] = useState<Errors>({});
  const [status, setStatus] = useState('');
  const nameRef = useRef<HTMLInputElement>(null);
  const descriptionRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    document.title = 'Playgroups · MTG Stats';
  }, []);

  const focusFirst = (found: Errors) => {
    if (found.name) nameRef.current?.focus();
    else if (found.description) descriptionRef.current?.focus();
  };

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (mutation.isPending) return;
    const found = validatePlaygroup(name, description);
    setErrors(found);
    setStatus('');
    if (found.name ?? found.description) {
      focusFirst(found);
      return;
    }
    mutation.mutate(
      { name: name.trim(), description },
      {
        onSuccess: (created) => {
          setName('');
          setDescription('');
          setStatus(`Created playgroup ${created.name}.`);
          nameRef.current?.focus();
        },
        onError: (error) => {
          if (error instanceof ApiError && error.code === 'validation_error') {
            const mapped = fieldErrorsFromDetails(error);
            const fieldErrors: Errors = {};
            if (mapped.name) fieldErrors.name = mapped.name;
            if (mapped.description) fieldErrors.description = mapped.description;
            if (fieldErrors.name ?? fieldErrors.description) {
              setErrors(fieldErrors);
              focusFirst(fieldErrors);
              return;
            }
          }
          useToastStore.getState().push('error', errorMessage(error));
        },
      },
    );
  };

  const playgroups = list.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <section aria-labelledby="page-title">
      <h2 id="page-title">Playgroups</h2>
      <section aria-labelledby="create-playgroup-title" className="auth-page">
        <h3 id="create-playgroup-title">Create a playgroup</h3>
        <form onSubmit={onSubmit} noValidate>
          <TextField
            id="playgroup-name"
            name="name"
            label="Name"
            autoComplete="off"
            value={name}
            error={errors.name ?? null}
            onChange={(event) => {
              setName(event.target.value);
            }}
            ref={nameRef}
          />
          <div className="field">
            <label htmlFor="playgroup-description">Description (optional)</label>
            <textarea
              id="playgroup-description"
              name="description"
              rows={3}
              value={description}
              aria-invalid={errors.description ? true : undefined}
              aria-describedby={errors.description ? 'playgroup-description-error' : undefined}
              onChange={(event) => {
                setDescription(event.target.value);
              }}
              ref={descriptionRef}
            />
            {errors.description ? (
              <p id="playgroup-description-error" role="alert" className="field-error">
                {errors.description}
              </p>
            ) : null}
          </div>
          <button type="submit" disabled={mutation.isPending}>
            {mutation.isPending ? 'Creating…' : 'Create playgroup'}
          </button>
          <p role="status">{status}</p>
        </form>
      </section>
      <section aria-labelledby="your-playgroups-title">
        <h3 id="your-playgroups-title">Your playgroups</h3>
        {list.isPending ? (
          <p role="status">Loading playgroups…</p>
        ) : list.isError ? (
          <p role="alert">
            {errorMessage(list.error)}{' '}
            <button
              type="button"
              onClick={() => {
                void list.refetch();
              }}
            >
              Retry
            </button>
          </p>
        ) : playgroups.length === 0 ? (
          <p>You are not in any playgroup yet.</p>
        ) : (
          <ul>
            {playgroups.map((p) => (
              <li key={p.id}>
                {p.name} ({p.role})
              </li>
            ))}
          </ul>
        )}
        {list.hasNextPage ? (
          <button
            type="button"
            disabled={list.isFetchingNextPage}
            onClick={() => {
              void list.fetchNextPage();
            }}
          >
            Load more
          </button>
        ) : null}
      </section>
    </section>
  );
}
