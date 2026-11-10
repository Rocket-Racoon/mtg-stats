import { infiniteQueryOptions, type QueryKey as TanstackQueryKey } from '@tanstack/react-query';

/** The `Page[T]` envelope of every list endpoint (S-1.4.7). */
export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface CursorPageConfig<TPage extends Page<unknown>, TKey extends TanstackQueryKey> {
  queryKey: TKey;
  /** Fetches one page. `cursor` is `null` for the first page. Pass it as `?cursor=`. */
  fetchPage: (cursor: string | null, signal: AbortSignal) => Promise<TPage>;
}

/**
 * Shared options for `useInfiniteQuery` over a cursor-paged list: the next page parameter is
 * `next_cursor`, and a `null` cursor means there are no further pages.
 */
export function cursorPageOptions<TPage extends Page<unknown>, TKey extends TanstackQueryKey>(
  config: CursorPageConfig<TPage, TKey>,
) {
  return infiniteQueryOptions({
    queryKey: config.queryKey,
    queryFn: ({ pageParam, signal }) => config.fetchPage(pageParam, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage: TPage): string | undefined => lastPage.next_cursor ?? undefined,
  });
}
