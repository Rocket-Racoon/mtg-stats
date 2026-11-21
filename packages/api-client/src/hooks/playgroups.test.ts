// @vitest-environment jsdom
// S-2.4.1: usePlaygroups() pages by cursor over GET /api/v1/playgroups; useCreatePlaygroup()
// POSTs {name, description}, invalidates the list and opts out of the global error toast.
import { QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import { createElement, type ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';
import {
  ApiClientProvider,
  ApiError,
  createApiClient,
  createQueryClient,
  playgroupKeys,
  useCreatePlaygroup,
  usePlaygroups,
} from '../index';

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
const item = (name: string) => ({
  id: name,
  name,
  description: '',
  role: 'owner',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  joined_at: '2026-01-01T00:00:00Z',
});

function setup(handler: (request: Request) => Response) {
  const urls: string[] = [];
  const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input, init);
    urls.push(`${request.method} ${new URL(request.url).pathname}${new URL(request.url).search}`);
    return handler(request);
  });
  const client = createApiClient({
    baseUrl: 'http://api.test',
    fetch: fetchImpl as unknown as typeof fetch,
  });
  const qc = createQueryClient();
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(
      QueryClientProvider,
      { client: qc },
      createElement(ApiClientProvider, { client }, children),
    );
  return { wrapper, qc, urls };
}

describe('playgroupKeys', () => {
  it('builds [playgroups, list, {}]', () => {
    expect(playgroupKeys.list()).toEqual(['playgroups', 'list', {}]);
  });
});

describe('usePlaygroups', () => {
  it('fetches the first page, then the next one with the cursor', async () => {
    const { wrapper, urls } = setup((request) =>
      new URL(request.url).searchParams.get('cursor') === 'c1'
        ? json({ items: [item('b')], next_cursor: null })
        : json({ items: [item('a')], next_cursor: 'c1' }),
    );
    const { result } = renderHook(() => usePlaygroups(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.hasNextPage).toBe(true);
    await act(async () => {
      await result.current.fetchNextPage();
    });
    await waitFor(() => {
      expect(result.current.data?.pages).toHaveLength(2);
    });
    expect(result.current.data?.pages.flatMap((p) => p.items.map((i) => i.name))).toEqual([
      'a',
      'b',
    ]);
    expect(result.current.hasNextPage).toBe(false);
    expect(urls[0]).toBe('GET /api/v1/playgroups');
    expect(urls[1]).toBe('GET /api/v1/playgroups?cursor=c1');
  });
});

describe('useCreatePlaygroup', () => {
  it('POSTs the body, resolves the playgroup and invalidates the list', async () => {
    let body: unknown = null;
    const { wrapper, qc } = setup((request) => {
      void request
        .clone()
        .json()
        .then((b: unknown) => (body = b));
      return json(item('Crew'), 201);
    });
    const invalidate = vi.spyOn(qc, 'invalidateQueries');
    const { result } = renderHook(() => useCreatePlaygroup(), { wrapper });
    let data: unknown;
    await act(async () => {
      data = await result.current.mutateAsync({ name: 'Crew', description: 'x' });
    });
    expect(data).toMatchObject({ name: 'Crew', role: 'owner' });
    expect(body).toEqual({ name: 'Crew', description: 'x' });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: playgroupKeys.list() });
    expect(qc.getMutationCache().getAll()[0]!.meta).toMatchObject({ errorToast: false });
  });

  it('rejects with the ApiError of a 422', async () => {
    const { wrapper } = setup(() =>
      json(
        { error: { code: 'validation_error', message: 'm', details: null, request_id: 'r' } },
        422,
      ),
    );
    const { result } = renderHook(() => useCreatePlaygroup(), { wrapper });
    await act(async () => {
      result.current.mutate({ name: 'ab', description: '' });
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(ApiError);
    expect(result.current.error).toMatchObject({ status: 422, code: 'validation_error' });
  });
});
