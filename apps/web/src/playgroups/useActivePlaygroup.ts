import { useEffect } from 'react';
import { ApiError, usePlaygroup, usePlaygroups, type Playgroup } from '@mtg-stats/api-client';
import { useActivePlaygroupStore } from './active-store';

/** The rows of a page; a malformed response (no `items` array) counts as an empty page. */
export function itemsOf<T>(page: { items: T[] }): T[] {
  return Array.isArray(page.items) ? page.items : [];
}

/** True for a 404 `not_found` (decided by `code`, never by message). */
export function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.code === 'not_found';
}

export interface ActivePlaygroup {
  status: 'loading' | 'error' | 'ready';
  /** The playgroups loaded so far, in the API order (plus the active one if it is further on). */
  playgroups: readonly Playgroup[];
  /** The reconciled active playgroup: null while loading, in error, or with no playgroups. */
  active: Playgroup | null;
  setActive: (playgroupId: string) => void;
  error: unknown;
  retry: () => void;
  /** More pages of the list exist; `loadMore` fetches the next one. */
  hasMore: boolean;
  loadingMore: boolean;
  loadMore: () => void;
  /** True once nothing is in flight, so `active` is final and may be written back to the store. */
  settled: boolean;
}

/**
 * The active playgroup, derived on every render so there is no flash of a stale group: the
 * remembered id if it is still one of the caller's playgroups, else the first one in the API order.
 * `useReconcileActivePlaygroup` writes the result back.
 *
 * It reads the shared, cursor-paged list (the same cache entry as the Playgroups page) and never
 * pages it on its own. When the remembered id is not on the loaded pages (a later page, a group
 * joined elsewhere, or one the caller left), the id is checked with `GET /playgroups/{id}` (200
 * means still a member, 404 means not) instead of downloading the whole list.
 */
export function useActivePlaygroup(): ActivePlaygroup {
  const list = usePlaygroups();
  const storedId = useActivePlaygroupStore((s) => s.activeId);
  const setActive = useActivePlaygroupStore((s) => s.setActive);
  const loaded = list.data?.pages.flatMap(itemsOf) ?? [];

  // A failed background refetch keeps the data it already has: only "error and no data" is an error.
  const hasList = list.data !== undefined;
  const storedLoaded = storedId !== null && loaded.some((p) => p.id === storedId);
  const needsCheck = hasList && storedId !== null && !storedLoaded;
  const check = usePlaygroup(storedId ?? '', { enabled: needsCheck });
  // Only a 404 means "no longer a member"; any other failure is an error and must not overwrite
  // the remembered id.
  const checkNotFound = needsCheck && check.isError && isNotFound(check.error);
  const checkFailed = needsCheck && check.isError && !checkNotFound && check.data === undefined;
  const checkMember = needsCheck && check.data !== undefined && !checkNotFound;

  let active: Playgroup | null = null;
  if (hasList) {
    if (storedLoaded) active = loaded.find((p) => p.id === storedId) ?? null;
    else if (checkMember) active = check.data ?? null;
    else active = loaded[0] ?? null;
  }
  const playgroups =
    active && !loaded.some((p) => p.id === active.id) ? [...loaded, active] : loaded;

  const checking = needsCheck && check.isPending;
  const status =
    (list.isError && !hasList) || checkFailed
      ? 'error'
      : !hasList || checking
        ? 'loading'
        : 'ready';
  return {
    status,
    playgroups,
    active,
    setActive,
    error: checkFailed ? check.error : list.error,
    // Re-run whatever failed: the list, and the membership check when it is in play.
    retry: () => {
      if (list.isError || !needsCheck) void list.refetch();
      if (needsCheck && check.isError) void check.refetch();
    },
    hasMore: list.hasNextPage,
    loadingMore: list.isFetchingNextPage,
    loadMore: () => {
      void list.fetchNextPage();
    },
    settled: status === 'ready' && !list.isFetching && !check.isFetching,
  };
}

/**
 * Overwrites the stored id when it no longer matches a playgroup of the caller (left, removed,
 * deleted), with the reconciled one, or clears it when there are none. Mounted once, by the
 * switcher in the app shell. Waits until nothing is in flight: a list that is refetching (for
 * example after joining) must not clear a freshly chosen id.
 */
export function useReconcileActivePlaygroup(): void {
  const { settled, active } = useActivePlaygroup();
  const storedId = useActivePlaygroupStore((s) => s.activeId);
  const setActive = useActivePlaygroupStore((s) => s.setActive);
  const resolved = active?.id ?? null;
  useEffect(() => {
    if (settled && resolved !== storedId) setActive(resolved);
  }, [settled, resolved, storedId, setActive]);
}
