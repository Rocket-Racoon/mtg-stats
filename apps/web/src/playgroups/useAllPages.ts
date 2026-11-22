import { useEffect } from 'react';

interface PagedQuery {
  data: { pages: unknown[] } | undefined;
  hasNextPage: boolean;
  isFetching: boolean;
  isFetchingNextPage: boolean;
  isError: boolean;
  fetchNextPage: (options: { cancelRefetch: boolean }) => unknown;
}

/**
 * Follows `next_cursor` until the last page, so a list is never silently truncated. Stops on an
 * error (the screen shows it with a Retry) instead of looping. The page count is a dependency
 * on purpose: a page that arrives between two renders must still trigger the next request.
 */
export function useFetchAllPages(query: PagedQuery): void {
  const { hasNextPage, isFetching, isFetchingNextPage, isError, fetchNextPage } = query;
  const pageCount = query.data?.pages.length ?? 0;
  useEffect(() => {
    if (hasNextPage && !isFetching && !isFetchingNextPage && !isError) {
      // Several components may follow the same list: never cancel a fetch another one started.
      void fetchNextPage({ cancelRefetch: false });
    }
  }, [pageCount, hasNextPage, isFetching, isFetchingNextPage, isError, fetchNextPage]);
}
