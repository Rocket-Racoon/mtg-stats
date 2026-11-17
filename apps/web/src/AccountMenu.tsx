import { useId, useRef, useState, type KeyboardEvent, type RefObject } from 'react';
import { useSession } from './session/session';
import { useSignOut, useSignOutEverywhere } from './session/useSignOut';
import { ConfirmDialog } from './ui/ConfirmDialog';

/**
 * Account menu (S-2.1.12), shown at the end of the navigation only while signed in. A disclosure
 * button: Enter or Space opens it, Tab walks the two actions, Escape closes it and returns focus
 * to the button. "Sign out everywhere" asks for confirmation in a focus-trapping dialog. The API
 * hooks live in the parts that appear when needed, so a signed-out shell needs no providers.
 */
export function AccountMenu() {
  const { status } = useSession();
  return status === 'signed_in' ? <SignedInAccountMenu /> : null;
}

function SignedInAccountMenu() {
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const listId = useId();

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === 'Escape' && open) {
      event.stopPropagation(); // do not also close the compact nav
      setOpen(false);
      toggleRef.current?.focus();
    }
  };

  return (
    <div className="account-menu" onKeyDown={onKeyDown}>
      <button
        ref={toggleRef}
        type="button"
        className="account-toggle"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        onClick={() => {
          setOpen((v) => !v);
        }}
      >
        Account menu
      </button>
      {open ? (
        <ul id={listId} className="account-menu-list">
          <li>
            <SignOutButton />
          </li>
          <li>
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                setConfirming(true);
              }}
            >
              Sign out everywhere
            </button>
          </li>
        </ul>
      ) : null}
      {confirming ? (
        <SignOutEverywhereDialog
          returnFocusRef={toggleRef}
          onCancel={() => {
            setConfirming(false);
          }}
        />
      ) : null}
    </div>
  );
}

function SignOutButton() {
  const { signOut, signingOut } = useSignOut();
  return (
    <button
      type="button"
      aria-disabled={signingOut}
      onClick={() => {
        if (!signingOut) void signOut();
      }}
    >
      {signingOut ? 'Signing out…' : 'Sign out'}
    </button>
  );
}

function SignOutEverywhereDialog({
  onCancel,
  returnFocusRef,
}: {
  onCancel: () => void;
  returnFocusRef: RefObject<HTMLElement | null>;
}) {
  const { signOutEverywhere, signOutHere, cancel, pending, error } = useSignOutEverywhere();
  return (
    <ConfirmDialog
      title="Sign out everywhere?"
      confirmLabel={pending ? 'Signing out…' : 'Sign out everywhere'}
      pending={pending}
      error={error}
      returnFocusRef={returnFocusRef}
      onCancel={() => {
        cancel(); // aborts a request still in flight
        onCancel();
      }}
      extraActions={
        error ? (
          <button
            type="button"
            aria-disabled={pending}
            onClick={() => {
              if (!pending) void signOutHere();
            }}
          >
            Sign out on this device only
          </button>
        ) : null
      }
      onConfirm={() => {
        void signOutEverywhere();
      }}
    >
      <p>This signs you out on every device where you are signed in, including this one.</p>
    </ConfirmDialog>
  );
}
