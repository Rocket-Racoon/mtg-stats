import * as tanstack from '@tanstack/react-query';
import { QueryClient, InfiniteQueryObserver } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  ApiError,
  QueryClientProvider,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  DOMAINS,
  MAX_RETRIES,
  NETWORK_ERROR,
  UNEXPECTED_RESPONSE,
  createQueryClient,
  cursorPageOptions,
  domainKey,
  queryKey,
  shouldRetry,
  type Page,
} from './index';

const err = (status: number, code = 'some_code') => new ApiError({ status, code, message: 'boom' });

describe('query key factory', () => {
  it('builds [domain, operation, params]', () => {
    expect(queryKey('decks', 'list', { q: 'x' })).toEqual(['decks', 'list', { q: 'x' }]);
  });

  it('defaults params to an empty object', () => {
    expect(queryKey('system', 'healthLive')).toEqual(['system', 'healthLive', {}]);
  });

  it('builds a domain key as [domain]', () => {
    expect(domainKey('games')).toEqual(['games']);
  });

  it('lists the ten architecture domains', () => {
    expect([...DOMAINS].sort()).toEqual(
      [
        'brackets',
        'cards',
        'collection',
        'decks',
        'games',
        'identity',
        'leagues',
        'playgroups',
        'stats',
        'system',
      ].sort(),
    );
  });

  it('invalidating [domain] invalidates every query of that domain only', async () => {
    const qc = new QueryClient();
    qc.setQueryData(queryKey('decks', 'list'), 1);
    qc.setQueryData(queryKey('decks', 'detail', { id: 'a' }), 2);
    qc.setQueryData(queryKey('games', 'list'), 3);
    await qc.invalidateQueries({ queryKey: domainKey('decks') });
    expect(qc.getQueryState(queryKey('decks', 'list'))?.isInvalidated).toBe(true);
    expect(qc.getQueryState(queryKey('decks', 'detail', { id: 'a' }))?.isInvalidated).toBe(true);
    expect(qc.getQueryState(queryKey('games', 'list'))?.isInvalidated).toBe(false);
  });
});

describe('retry rules', () => {
  it.each([400, 401, 404, 422, 499])('does not retry a %i', (status) => {
    expect(shouldRetry(0, err(status))).toBe(false);
  });

  it.each([500, 503])('retries a %i up to the limit', (status) => {
    expect(shouldRetry(0, err(status))).toBe(true);
    expect(shouldRetry(MAX_RETRIES - 1, err(status))).toBe(true);
    expect(shouldRetry(MAX_RETRIES, err(status))).toBe(false);
  });

  it('retries a network error up to 2 times', () => {
    const net = err(0, NETWORK_ERROR);
    expect(shouldRetry(0, net)).toBe(true);
    expect(shouldRetry(1, net)).toBe(true);
    expect(shouldRetry(2, net)).toBe(false);
  });

  it('does not retry an unexpected 2xx response or a non-ApiError', () => {
    expect(shouldRetry(0, err(200, UNEXPECTED_RESPONSE))).toBe(false);
    expect(shouldRetry(0, new TypeError('bug'))).toBe(false);
  });

  it('configures the default QueryClient accordingly', () => {
    const qc = createQueryClient();
    expect(qc.getDefaultOptions().queries?.retry).toBe(shouldRetry);
    expect(qc.getDefaultOptions().mutations?.retry).toBe(false);
  });

  it('merges overrides over the defaults', () => {
    const qc = createQueryClient({ defaultOptions: { queries: { staleTime: 5 } } });
    expect(qc.getDefaultOptions().queries?.staleTime).toBe(5);
    expect(qc.getDefaultOptions().queries?.retry).toBe(shouldRetry);
  });

  it('ignores undefined overrides instead of dropping the default retry rule', () => {
    const qc = createQueryClient({
      defaultOptions: { queries: { retry: undefined }, mutations: { retry: undefined } },
    });
    expect(qc.getDefaultOptions().queries?.retry).toBe(shouldRetry);
    expect(qc.getDefaultOptions().mutations?.retry).toBe(false);
  });

  it('retries a failing 5xx query exactly twice and a 4xx never', async () => {
    const run = async (error: ApiError) => {
      const qc = createQueryClient({ defaultOptions: { queries: { retryDelay: 0 } } });
      const queryFn = vi.fn().mockRejectedValue(error);
      await qc.fetchQuery({ queryKey: ['t'], queryFn }).catch(() => undefined);
      return queryFn.mock.calls.length;
    };
    expect(await run(err(503))).toBe(3);
    expect(await run(err(404))).toBe(1);
  });
});

describe('cursorPageOptions', () => {
  const pages: Record<string, Page<number>> = {
    first: { items: [1, 2], next_cursor: 'c2' },
    c2: { items: [3], next_cursor: null },
  };
  let fetchPage: ReturnType<typeof makeFetchPage>;
  const makeFetchPage = () =>
    vi.fn((cursor: string | null) => Promise.resolve(pages[cursor ?? 'first'] as Page<number>));
  beforeEach(() => {
    fetchPage = makeFetchPage();
  });

  it('starts with a null cursor and passes next_cursor as the next page param', () => {
    const options = cursorPageOptions({ queryKey: queryKey('decks', 'list'), fetchPage });
    expect(options.initialPageParam).toBeNull();
    const next = options.getNextPageParam as (p: Page<number>) => unknown;
    expect(next(pages.first as Page<number>)).toBe('c2');
  });

  it('reports no further pages when next_cursor is null', () => {
    const options = cursorPageOptions({ queryKey: queryKey('decks', 'list'), fetchPage });
    const next = options.getNextPageParam as (p: Page<number>) => unknown;
    expect(next(pages.c2 as Page<number>)).toBeUndefined();
  });

  it('walks the pages through an infinite query', async () => {
    const qc = new QueryClient();
    const observer = new InfiniteQueryObserver(
      qc,
      cursorPageOptions({ queryKey: queryKey('decks', 'list'), fetchPage }),
    );
    let result = await observer.fetchNextPage();
    expect(result.hasNextPage).toBe(true);
    result = await observer.fetchNextPage();
    expect(result.data?.pages.flatMap((p) => p.items)).toEqual([1, 2, 3]);
    expect(result.hasNextPage).toBe(false);
    expect(fetchPage.mock.calls.map((c) => c[0])).toEqual([null, 'c2']);
  });
});

describe('TanStack re-exports', () => {
  it('are the same objects as @tanstack/react-query', () => {
    expect(QueryClientProvider).toBe(tanstack.QueryClientProvider);
    expect(useQueryClient).toBe(tanstack.useQueryClient);
    expect(useQuery).toBe(tanstack.useQuery);
    expect(useInfiniteQuery).toBe(tanstack.useInfiniteQuery);
    expect(useMutation).toBe(tanstack.useMutation);
  });
});
