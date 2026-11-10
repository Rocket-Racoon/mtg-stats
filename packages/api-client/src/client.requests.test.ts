// Request building and response handling, against a hand-written `paths` type that exercises
// path params, query params, JSON bodies and empty responses (the generated spec has none yet).
import { describe, expect, expectTypeOf, it, vi } from 'vitest';
import { ApiError, createApiClient } from './index';

interface Deck {
  id: string;
  name: string;
}

interface TestPaths {
  '/api/v1/decks': {
    get: {
      parameters: { query?: { limit?: number; q?: string }; header?: never; path?: never };
      requestBody?: never;
      responses: {
        200: {
          headers: Record<string, unknown>;
          content: { 'application/json': { items: Deck[] } };
        };
      };
    };
    post: {
      parameters: { query?: never; header?: never; path?: never };
      requestBody: { content: { 'application/json': { name: string } } };
      responses: {
        201: { headers: Record<string, unknown>; content: { 'application/json': Deck } };
      };
    };
  };
  '/api/v1/decks/{deck_id}': {
    get: {
      parameters: { query?: never; header?: never; path: { deck_id: string } };
      requestBody?: never;
      responses: {
        200: { headers: Record<string, unknown>; content: { 'application/json': Deck } };
      };
    };
    delete: {
      parameters: { query?: never; header?: never; path: { deck_id: string } };
      requestBody?: never;
      responses: { 204: { headers: Record<string, unknown>; content?: never } };
    };
  };
}

const BASE = 'https://api.test';

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init.headers },
  });
}

function mockFetch(make: () => Response = () => jsonResponse({ items: [] })) {
  return vi.fn<(req: Request) => Promise<Response>>(async () => make());
}

function build(
  fetchMock: ReturnType<typeof mockFetch>,
  extra: { baseUrl?: string; headers?: Record<string, string> } = {},
) {
  return createApiClient<TestPaths>({
    baseUrl: extra.baseUrl ?? BASE,
    fetch: fetchMock as unknown as typeof fetch,
    headers: extra.headers,
  });
}

async function caught(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  throw new Error('expected the call to throw');
}

describe('request building', () => {
  it.each([BASE, `${BASE}/`])('joins baseUrl %s with the operation path', async (baseUrl) => {
    const fetchMock = mockFetch();
    await build(fetchMock, { baseUrl }).GET('/api/v1/decks');
    expect(fetchMock.mock.calls[0]![0].url).toBe(`${BASE}/api/v1/decks`);
  });

  it('substitutes path params into the URL', async () => {
    const fetchMock = mockFetch(() => jsonResponse({ id: 'd1', name: 'Atraxa' }));
    const data = await build(fetchMock).GET('/api/v1/decks/{deck_id}', {
      params: { path: { deck_id: 'd1' } },
    });
    expect(fetchMock.mock.calls[0]![0].url).toBe(`${BASE}/api/v1/decks/d1`);
    expect(data).toEqual({ id: 'd1', name: 'Atraxa' });
  });

  it('serializes query params onto the URL', async () => {
    const fetchMock = mockFetch();
    await build(fetchMock).GET('/api/v1/decks', { params: { query: { limit: 5, q: 'a b' } } });
    const url = new URL(fetchMock.mock.calls[0]![0].url);
    expect(url.pathname).toBe('/api/v1/decks');
    expect(url.searchParams.get('limit')).toBe('5');
    expect(url.searchParams.get('q')).toBe('a b');
  });

  it('sends a JSON body with the application/json content type', async () => {
    const fetchMock = mockFetch(() => jsonResponse({ id: 'd2', name: 'Korvold' }, { status: 201 }));
    const data = await build(fetchMock).POST('/api/v1/decks', { body: { name: 'Korvold' } });
    const request = fetchMock.mock.calls[0]![0];
    expect(request.method).toBe('POST');
    expect(request.headers.get('Content-Type')).toContain('application/json');
    expect(await request.json()).toEqual({ name: 'Korvold' });
    expect(data).toEqual({ id: 'd2', name: 'Korvold' });
  });

  it('sends default headers on every request', async () => {
    const fetchMock = mockFetch();
    const api = build(fetchMock, { headers: { 'X-Client': 'web', 'Accept-Language': 'en' } });
    await api.GET('/api/v1/decks');
    await api.GET('/api/v1/decks');
    expect(fetchMock).toHaveBeenCalledTimes(2);
    for (const [request] of fetchMock.mock.calls) {
      expect(request.headers.get('X-Client')).toBe('web');
      expect(request.headers.get('Accept-Language')).toBe('en');
    }
  });

  it('uses the custom fetch and not the global one', async () => {
    const globalMock = mockFetch();
    vi.stubGlobal('fetch', globalMock);
    try {
      const custom = mockFetch();
      await build(custom).GET('/api/v1/decks');
      expect(custom).toHaveBeenCalledTimes(1);
      expect(globalMock).not.toHaveBeenCalled();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('keeps clients independent (no global singleton)', async () => {
    const a = mockFetch();
    const b = mockFetch();
    await build(a, { headers: { 'X-Who': 'a' } }).GET('/api/v1/decks');
    await build(b, { headers: { 'X-Who': 'b' } }).GET('/api/v1/decks');
    expect(a.mock.calls[0]![0].headers.get('X-Who')).toBe('a');
    expect(b.mock.calls[0]![0].headers.get('X-Who')).toBe('b');
  });

  it('exposes GET, POST, PUT, PATCH and DELETE', () => {
    const api = build(mockFetch());
    for (const name of ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'] as const) {
      expect(typeof api[name]).toBe('function');
    }
  });
});

describe('response handling', () => {
  it('resolves a 204 response without throwing', async () => {
    const fetchMock = mockFetch(() => new Response(null, { status: 204 }));
    const data = await build(fetchMock).DELETE('/api/v1/decks/{deck_id}', {
      params: { path: { deck_id: 'd1' } },
    });
    expect(fetchMock.mock.calls[0]![0].method).toBe('DELETE');
    expect(data).toBeUndefined();
  });

  it('keeps a details object intact and null details as null', async () => {
    const withDetails = mockFetch(() =>
      jsonResponse(
        { error: { code: 'conflict', message: 'm', details: { a: [1, 2] }, request_id: 'r' } },
        { status: 409 },
      ),
    );
    const e1 = (await caught(build(withDetails).GET('/api/v1/decks'))) as ApiError;
    expect(e1).toBeInstanceOf(ApiError);
    expect(e1.details).toEqual({ a: [1, 2] });
    expect(e1.requestId).toBe('r');

    const nullDetails = mockFetch(() =>
      jsonResponse({ error: { code: 'conflict', message: 'm', details: null } }, { status: 409 }),
    );
    const e2 = (await caught(build(nullDetails).GET('/api/v1/decks'))) as ApiError;
    expect(e2.details).toBeNull();
    expect(e2.requestId).toBeNull();
  });

  it.each([
    ['an envelope without a code', { error: { message: 'm', details: null } }],
    ['an envelope without a message', { error: { code: 'x', details: null } }],
    ['a non-object error', { error: 'boom' }],
    ['a null error', { error: null }],
    ['no error key', { detail: 'nope' }],
    ['a JSON array', [1, 2]],
  ])('maps %s to unexpected_response', async (_label, body) => {
    const fetchMock = mockFetch(() =>
      jsonResponse(body, { status: 400, headers: { 'X-Request-ID': 'hdr' } }),
    );
    const error = (await caught(build(fetchMock).GET('/api/v1/decks'))) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 400, code: 'unexpected_response', requestId: 'hdr' });
  });

  it('rejects a malformed JSON error body as unexpected_response', async () => {
    const fetchMock = mockFetch(
      () =>
        new Response('{"error": ', {
          status: 500,
          headers: { 'Content-Type': 'application/json' },
        }),
    );
    const error = await caught(build(fetchMock).GET('/api/v1/decks'));
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 500, code: 'unexpected_response' });
  });

  it.each([400, 401, 403, 404, 409, 422, 429, 500, 503])(
    'carries HTTP status %i on the ApiError',
    async (status) => {
      const fetchMock = mockFetch(() =>
        jsonResponse(
          { error: { code: 'c', message: 'm', details: null, request_id: 'r' } },
          { status },
        ),
      );
      const error = (await caught(build(fetchMock).GET('/api/v1/decks'))) as ApiError;
      expect(error.status).toBe(status);
    },
  );

  it('types the resolved value from the response schema', async () => {
    const fetchMock = mockFetch(() => jsonResponse({ items: [{ id: 'd1', name: 'A' }] }));
    const data = await build(fetchMock).GET('/api/v1/decks');
    expectTypeOf(data).toEqualTypeOf<{ items: Deck[] }>();
  });

  it('wraps a non-Error rejection as network_error keeping it as the cause', async () => {
    const api = createApiClient<TestPaths>({
      baseUrl: BASE,
      fetch: (async () => {
        throw 'offline';
      }) as typeof fetch,
    });
    const error = (await caught(api.GET('/api/v1/decks'))) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 0, code: 'network_error' });
    expect(error.cause).toBe('offline');
  });
});
