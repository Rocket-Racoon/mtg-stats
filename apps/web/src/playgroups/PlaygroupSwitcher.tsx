import { useId, useRef, useState, type KeyboardEvent } from 'react';
import { Link, matchPath, useLocation, useNavigate } from 'react-router';
import { useSession } from '../session/session';
import { useActivePlaygroup, useReconcileActivePlaygroup } from './useActivePlaygroup';

/** Closed or open, and (while open) which option is highlighted. */
type Highlight = number | null;

/**
 * Playgroup switcher (S-2.4.5), at the top of the signed-in shell's page area. Mounted only while
 * signed in, so a signed-out shell needs no providers.
 */
export function PlaygroupSwitcher() {
  const { status } = useSession();
  return status === 'signed_in' ? <SignedInSwitcher /> : null;
}

function SignedInSwitcher() {
  useReconcileActivePlaygroup();
  const { status, playgroups, active, setActive, retry, hasMore, loadingMore, loadMore } =
    useActivePlaygroup();
  const navigate = useNavigate();
  const { pathname } = useLocation();

  const select = (playgroupId: string) => {
    setActive(playgroupId);
    // On a playgroup's own home the URL names the group, so follow the choice.
    if (pathname !== '/playgroups/join' && matchPath('/playgroups/:playgroupId', pathname)) {
      void navigate(`/playgroups/${playgroupId}`);
    }
  };

  return (
    <div className="playgroup-switcher" role="group" aria-label="Playgroup">
      {/* Plain text, not live regions: the shell must not announce itself on every page. */}
      {status === 'loading' ? <p>Loading playgroups…</p> : null}
      {status === 'error' ? (
        <p>
          Could not load your playgroups.{' '}
          <button type="button" onClick={retry}>
            Try again
          </button>
        </p>
      ) : null}
      {status === 'ready' && playgroups.length > 0 ? (
        <PlaygroupListbox playgroups={playgroups} activeId={active?.id ?? null} onSelect={select} />
      ) : null}
      {status === 'ready' && hasMore ? (
        <button type="button" disabled={loadingMore} onClick={loadMore}>
          {loadingMore ? 'Loading…' : 'Load more playgroups'}
        </button>
      ) : null}
      {/* With no playgroups the home page's empty state offers this; two links would be a twin. */}
      {status === 'ready' && playgroups.length > 0 ? (
        <Link to="/playgroups">Create a playgroup</Link>
      ) : null}
    </div>
  );
}

interface ListboxProps {
  playgroups: readonly { id: string; name: string }[];
  activeId: string | null;
  onSelect: (playgroupId: string) => void;
}

/**
 * The "select-only combobox" pattern (WAI-ARIA APG): the trigger is a button with role
 * `combobox` and keeps focus the whole time; the highlighted option is named by
 * `aria-activedescendant`. Closed: Enter, Space, ArrowDown or ArrowUp open it on the active
 * playgroup; Home and End open it on the first or last. Open: ArrowDown and ArrowUp move (they
 * stop at the ends), Home and End jump, Enter or Space select and close, Escape closes without
 * changing anything and keeps focus on the trigger, Tab and a click elsewhere close it without
 * selecting. Clicking or tapping an option selects it.
 */
function PlaygroupListbox({ playgroups, activeId, onSelect }: ListboxProps) {
  const [highlight, setHighlight] = useState<Highlight>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const baseId = useId();
  const listId = `${baseId}-list`;
  const labelId = `${baseId}-label`;
  const optionId = (index: number) => `${baseId}-option-${String(index)}`;
  const open = highlight !== null;
  const activeIndex = Math.max(
    0,
    playgroups.findIndex((p) => p.id === activeId),
  );
  const active = playgroups.find((p) => p.id === activeId);
  const last = playgroups.length - 1;

  const close = () => {
    setHighlight(null);
  };
  const choose = (index: number) => {
    const target = playgroups[index];
    close();
    if (target && target.id !== activeId) onSelect(target.id);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    const { key } = event;
    if (!open) {
      if (key === 'Enter' || key === ' ' || key === 'ArrowDown' || key === 'ArrowUp') {
        event.preventDefault();
        setHighlight(activeIndex);
      } else if (key === 'Home') {
        event.preventDefault();
        setHighlight(0);
      } else if (key === 'End') {
        event.preventDefault();
        setHighlight(last);
      }
      return;
    }
    switch (key) {
      case 'ArrowDown':
        event.preventDefault();
        setHighlight(Math.min(highlight + 1, last));
        break;
      case 'ArrowUp':
        event.preventDefault();
        setHighlight(Math.max(highlight - 1, 0));
        break;
      case 'Home':
        event.preventDefault();
        setHighlight(0);
        break;
      case 'End':
        event.preventDefault();
        setHighlight(last);
        break;
      case 'Enter':
      case ' ':
        event.preventDefault();
        choose(highlight);
        break;
      case 'Escape':
        event.preventDefault();
        event.stopPropagation(); // do not also close the compact navigation
        close();
        triggerRef.current?.focus();
        break;
      case 'Tab':
        close();
        break;
      default:
    }
  };

  return (
    <div className="playgroup-switcher-field">
      <span id={labelId} className="playgroup-switcher-label">
        Playgroup
      </span>
      <button
        ref={triggerRef}
        type="button"
        role="combobox"
        className="playgroup-switcher-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        aria-labelledby={`${labelId} ${baseId}-value`}
        aria-activedescendant={open ? optionId(highlight) : undefined}
        onKeyDown={onKeyDown}
        onBlur={close}
        onClick={() => {
          setHighlight(open ? null : activeIndex);
        }}
      >
        <span id={`${baseId}-value`}>{active?.name ?? 'Choose a playgroup'}</span>
      </button>
      <div
        id={listId}
        role="listbox"
        aria-labelledby={labelId}
        className="playgroup-switcher-list"
        hidden={!open}
      >
        {open
          ? playgroups.map((playgroup, index) => (
              <div
                key={playgroup.id}
                id={optionId(index)}
                role="option"
                aria-selected={playgroup.id === activeId}
                data-highlighted={index === highlight}
                className="playgroup-switcher-option"
                // Keep focus on the trigger: a blur would close the list before the click lands.
                onMouseDown={(event) => {
                  event.preventDefault();
                }}
                onMouseMove={() => {
                  setHighlight(index);
                }}
                onClick={() => {
                  choose(index);
                  triggerRef.current?.focus();
                }}
              >
                {playgroup.name}
              </div>
            ))
          : null}
      </div>
    </div>
  );
}
