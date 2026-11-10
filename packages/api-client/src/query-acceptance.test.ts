// @vitest-environment jsdom
import {
  QueryClientProvider,
  hashKey,
  useInfiniteQuery,
  useMutation,
  useQuery,
  type QueryClient,
} from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import { createElement, type ReactNode } from 'react';
import { describe, expect, expectTypeOf, it, vi } from 'vitest';
import {
  ApiClientProvider,
  DOMAINS,
  MAX_RETRIES,
  createApiClient,
  createQueryClient,
  cursorPageOptions,
  domainKey,
  queryKey,
  systemKeys,
  useApiClient,
  useHealthLive,
  type Page,
} from './index';

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });

const envelope = (code: string) => ({
  error: { code, message: 'm', details: null, request_id: 'r' },
});

function makeWrapper(
  qc: QueryClient,
  fetchImpl: typeof fetch = vi.fn() as unknown as typeof fetch,
) {
  const client = createApiClient({ baseUrl: 'http://api.test', fetch: fetchImpl });
  return ({ children }: { children: ReactNode }) =>
    createElement(
      QueryClientProvider,
      { client: qc },
      createElement(ApiClientProvider, { client }, children),
    );
}

describe('AC1: query key factory and domain invalidation', () => {
  it.each(DOMAINS)('domainKey(%s) is a one-element prefix of its keys', (domain) => {
    expect(domainKey(domain)).toEqual([domain]);
    expect(queryKey(domain, 'op', { a: 1 })).toEqual([domain, 'op', { a: 1 }]);
    expect(queryKey(domain, 'op')).toEqual([domain, 'op', {}]);
  });

  it('has exactly the ten architecture domains', () => {
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

  it('hashes keys with equal params identically regardless of param order', () => {
    expect(hashKey([...queryKey('decks', 'list', { a: 1, b: 2 })])).toBe(
      hashKey([...queryKey('decks', 'list', { b: 2, a: 1 })]),
    );
    expect(hashKey([...queryKey('decks', 'list', { a: 1 })])).not.toBe(
      hashKey([...queryKey('decks', 'list', { a: 2 })]),
    );
  });

  it('rejects an unknown domain at compile time', () => {
    // @ts-expect-error 'nope' is not a Domain
    expect(queryKey('nope', 'op')[0]).toBe('nope');
    // @ts-expect-error 'nope' is not a Domain
    expect(domainKey('nope')).toEqual(['nope']);
  });

  it('invalidating [domain] refetches active queries of that domain only', async () => {
    const qc = createQueryClient();
    const decksFn = vi.fn(() => Promise.resolve('d'));
    const decks2Fn = vi.fn(() => Promise.resolve('d2'));
    const gamesFn = vi.fn(() => Promise.resolve('g'));
    renderHook(
      () => {
        useQuery({ queryKey: queryKey('decks', 'list'), queryFn: decksFn });
        useQuery({ queryKey: queryKey('decks', 'detail', { id: '1' }), queryFn: decks2Fn });
        useQuery({ queryKey: queryKey('games', 'list'), queryFn: gamesFn });
      },
      { wrapper: makeWrapper(qc) },
    );
    await waitFor(() => {
      expect(decksFn).toHaveBeenCalledTimes(1);
      expect(decks2Fn).toHaveBeenCalledTimes(1);
      expect(gamesFn).toHaveBeenCalledTimes(1);
    });
    await act(() => qc.invalidateQueries({ queryKey: [...domainKey('decks')] }));
    expect(decksFn).toHaveBeenCalledTimes(2);
    expect(decks2Fn).toHaveBeenCalledTimes(2);
    expect(gamesFn).toHaveBeenCalledTimes(1);
  });
});

describe('AC2: retry policy through a real ApiError', () => {
  async function run(fetchImpl: typeof fetch, overrides?: Parameters<typeof createQueryClient>[0]) {
    const qc = createQueryClient({
      ...overrides,
      defaultOptions: {
        ...overrides?.defaultOptions,
        queries: { retryDelay: 0, ...overrides?.defaultOptions?.queries },
      },
    });
    const { result } = renderHook(
      () => {
        const client = useApiClient();
        return useQuery({
          queryKey: queryKey('system', 'x'),
          queryFn: () => client.GET('/api/v1/health/live'),
        });
      },
      { wrapper: makeWrapper(qc, fetchImpl) },
    );
    await waitFor(() => expect(result.current.isError).toBe(true), { timeout: 5000 });
    return result.current;
  }

  it('MAX_RETRIES is 2', () => {
    expect(MAX_RETRIES).toBe(2);
  });

  it.each([400, 401, 403, 404, 409, 422, 429, 499])('does not retry a %i', async (status) => {
    const f = vi.fn(() => Promise.resolve(json(envelope('client_err'), status)));
    const r = await run(f as unknown as typeof fetch);
    expect(f).toHaveBeenCalledTimes(1);
    expect(r.error).toMatchObject({ status });
  });

  it.each([500, 502, 503])('retries a %i twice (3 calls total)', async (status) => {
    const f = vi.fn(() => Promise.resolve(json(envelope('server_err'), status)));
    await run(f as unknown as typeof fetch);
    expect(f).toHaveBeenCalledTimes(3);
  });

  it('retries a network failure twice (3 calls total)', async () => {
    const f = vi.fn(() => Promise.reject(new TypeError('Failed to fetch')));
    const r = await run(f as unknown as typeof fetch);
    expect(f).toHaveBeenCalledTimes(3);
    expect(r.error).toMatchObject({ code: 'network_error', status: 0 });
  });

  it('does not retry a 200 with a non-JSON body', async () => {
    const f = vi.fn(() => Promise.resolve(new Response('<html></html>', { status: 200 })));
    const r = await run(f as unknown as typeof fetch);
    expect(f).toHaveBeenCalledTimes(1);
    expect(r.error).toMatchObject({ code: 'unexpected_response' });
  });

  it('does not retry a non-ApiError thrown by the query function', async () => {
    const qc = createQueryClient({ defaultOptions: { queries: { retryDelay: 0 } } });
    const fn = vi.fn(() => Promise.reject(new Error('bug')));
    const { result } = renderHook(
      () => useQuery({ queryKey: queryKey('system', 'y'), queryFn: fn }),
      { wrapper: makeWrapper(qc) },
    );
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('lets createQueryClient overrides replace the retry rule', async () => {
    const f = vi.fn(() => Promise.resolve(json(envelope('server_err'), 503)));
    await run(f as unknown as typeof fetch, { defaultOptions: { queries: { retry: false } } });
    expect(f).toHaveBeenCalledTimes(1);
  });

  it('keeps a 30 s staleTime by default', () => {
    expect(createQueryClient().getDefaultOptions().queries?.staleTime).toBe(30_000);
  });

  it('does not retry a mutation on a 503', async () => {
    const f = vi.fn(() => Promise.resolve(json(envelope('server_err'), 503)));
    const { result } = renderHook(
      () => {
        const client = useApiClient();
        return useMutation({ mutationFn: () => client.GET('/api/v1/health/live') });
      },
      { wrapper: makeWrapper(createQueryClient(), f as unknown as typeof fetch) },
    );
    await act(async () => {
      await result.current.mutateAsync().catch(() => undefined);
    });
    expect(f).toHaveBeenCalledTimes(1);
  });
});

describe('AC4: cursor pagination options', () => {
  interface Item {
    id: number;
  }
  const pages: Record<string, Page<Item>> = {
    first: { items: [{ id: 1 }], next_cursor: 'c2' },
    c2: { items: [{ id: 2 }], next_cursor: 'c3' },
    c3: { items: [{ id: 3 }], next_cursor: null },
  };

  function mount(fetchPage: (c: string | null, s: AbortSignal) => Promise<Page<Item>>) {
    return renderHook(
      () => useInfiniteQuery(cursorPageOptions({ queryKey: queryKey('decks', 'list'), fetchPage })),
      { wrapper: makeWrapper(createQueryClient()) },
    );
  }

  it('passes each previous next_cursor and stops when it is null', async () => {
    const fetchPage = vi.fn((c: string | null) =>
      Promise.resolve(pages[c ?? 'first'] as Page<Item>),
    );
    const { result } = mount(fetchPage);
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.hasNextPage).toBe(true);
    await act(() => result.current.fetchNextPage());
    await act(() => result.current.fetchNextPage());
    await waitFor(() => expect(result.current.data?.pages).toHaveLength(3));
    expect(result.current.hasNextPage).toBe(false);
    expect(fetchPage.mock.calls.map((c) => c[0])).toEqual([null, 'c2', 'c3']);
    expect(result.current.data?.pages.flatMap((p) => p.items.map((i) => i.id))).toEqual([1, 2, 3]);
  });

  it('reports no next page for an empty first page with next_cursor null', async () => {
    const fetchPage = vi.fn<(c: string | null, s: AbortSignal) => Promise<Page<Item>>>(() =>
      Promise.resolve({ items: [], next_cursor: null } as Page<Item>),
    );
    const { result } = mount(fetchPage);
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.hasNextPage).toBe(false);
    expect(fetchPage).toHaveBeenCalledTimes(1);
    expect(fetchPage.mock.calls[0]?.[0]).toBeNull();
  });

  it('passes an AbortSignal to fetchPage', async () => {
    const fetchPage = vi.fn<(c: string | null, s: AbortSignal) => Promise<Page<Item>>>(() =>
      Promise.resolve(pages['c3'] as Page<Item>),
    );
    const { result } = mount(fetchPage);
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(fetchPage.mock.calls[0]?.[1]).toBeInstanceOf(AbortSignal);
  });

  it('starts from a null page param under the given key', () => {
    const opts = cursorPageOptions({
      queryKey: queryKey('decks', 'list'),
      fetchPage: () => Promise.resolve(pages['c3'] as Page<Item>),
    });
    expect(opts.queryKey).toEqual(['decks', 'list', {}]);
    expect(opts.initialPageParam).toBeNull();
  });
});

describe('AC3: system health hook worked example', () => {
  it('keeps system keys under the system domain', () => {
    expect(systemKeys.healthLive()[0]).toBe('system');
    expect(systemKeys.healthReady()[0]).toBe('system');
  });

  it('useHealthLive returns typed data', () => {
    const { result } = renderHook(() => useHealthLive(), {
      wrapper: makeWrapper(createQueryClient()),
    });
    expectTypeOf(result.current.data).toEqualTypeOf<{ status: 'ok' } | undefined>();
  });
});
