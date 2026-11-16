// S-1.5.3: Bearer header, single-flight refresh with retry-once, cookie and body modes.
// Written from the acceptance criteria and decisions on issue #28, with a fake fetch that records
// every request (a refresh may reach fetch as a Request or as (url, init), so both are normalised).
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, createApiClient, REFRESH_PATH } from './index';
import type { TokenStore } from './index';

interface Ok {
  headers: Record<string, unknown>;
  content: { 'application/json': { ok: boolean } };
}
interface NoBodyPost {
  post: {
    parameters: { query?: never; header?: never; path?: never };
    requestBody?: never;
    responses: { 200: Ok };
  };
}
interface TestPaths {
  '/api/v1/decks': {
    get: {
      parameters: { query?: never; header?: never; path?: never };
      requestBody?: never;
      responses: { 200: Ok };
    };
    post: {
      parameters: { query?: never; header?: never; path?: never };
      requestBody: { content: { 'application/json': { name: string } } };
      responses: { 200: Ok };
    };
  };
  '/api/v1/auth/login': NoBodyPost;
  '/api/v1/auth/refresh': NoBodyPost;
  '/api/v1/auth/logout': NoBodyPost;
  '/api/v1/auth/logout-all': NoBodyPost;
}

const BASE = 'https://api.test';
const DECKS = '/api/v1/decks';

interface Rec {
  url: string;
  path: string;
  method: string;
  headers: Headers;
  credentials: RequestCredentials;
  body: string;
}

type Handler = (rec: Rec) => Response | Promise<Response>;

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init.headers },
  });
}

function errorResponse(status: number, code: string): Response {
  return jsonResponse(
    { error: { code, message: `${code} message`, details: null, request_id: `req-${code}` } },
    { status },
  );
}

function tokenResponse(access: string, refresh?: string): Response {
  return jsonResponse({
    access_token: access,
    token_type: 'bearer',
    expires_in: 900,
    ...(refresh === undefined ? {} : { refresh_token: refresh }),
  });
}

const okResponse = () => jsonResponse({ ok: true });

async function record(input: RequestInfo | URL, init?: RequestInit): Promise<Rec> {
  const request = new Request(input, init);
  return {
    url: request.url,
    path: new URL(request.url).pathname,
    method: request.method,
    headers: request.headers,
    credentials: request.credentials,
    body: await request.text(),
  };
}

function fakeFetch(handler: Handler) {
  const calls: Rec[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const rec = await record(input, init);
    calls.push(rec);
    return handler(rec);
  });
  return {
    fetch: fetchMock as unknown as typeof fetch,
    calls,
    refreshCalls: () => calls.filter((c) => c.path === REFRESH_PATH),
    apiCalls: (path = DECKS) => calls.filter((c) => c.path === path),
  };
}

interface Store extends TokenStore {
  access: string | null;
  refresh: string | null;
  setTokens: ReturnType<typeof vi.fn<TokenStore['setTokens']>>;
  clear: ReturnType<typeof vi.fn<TokenStore['clear']>>;
}

function memStore(
  init: { access?: string | null; refresh?: string | null; body?: boolean } = {},
): Store {
  const store: Store = {
    access: init.access === undefined ? 'old' : init.access,
    refresh: init.refresh ?? null,
    getAccessToken: () => store.access,
    setTokens: vi.fn<TokenStore['setTokens']>((tokens) => {
      store.access = tokens.accessToken;
      if (tokens.refreshToken !== undefined) store.refresh = tokens.refreshToken;
    }),
    clear: vi.fn<TokenStore['clear']>(() => {
      store.access = null;
      store.refresh = null;
    }),
  };
  if (init.body) store.getRefreshToken = () => store.refresh;
  return store;
}

/** A server where only `Bearer new*` tokens work on decks and the refresh returns `refresh`. */
function server(refresh: Handler = () => tokenResponse('new')) {
  return fakeFetch(async (rec) => {
    if (rec.path === REFRESH_PATH) {
      await new Promise((resolve) => setTimeout(resolve, 5));
      return refresh(rec);
    }
    const auth = rec.headers.get('Authorization') ?? '';
    return auth.startsWith('Bearer new') ? okResponse() : errorResponse(401, 'token_expired');
  });
}

function build(
  fetchImpl: typeof fetch,
  extra: { tokenStore?: TokenStore; onSessionExpired?: () => void } = {},
) {
  return createApiClient<TestPaths>({ baseUrl: BASE, fetch: fetchImpl, ...extra });
}

async function caught(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  throw new Error('expected the call to throw');
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('REFRESH_PATH', () => {
  it('is the auth refresh endpoint', () => {
    expect(REFRESH_PATH).toBe('/api/v1/auth/refresh');
  });
});

describe('AC1: Bearer header', () => {
  it('sends Authorization: Bearer <token> when the store has an access token', async () => {
    const srv = fakeFetch(okResponse);
    await build(srv.fetch, { tokenStore: memStore({ access: 'abc' }) }).GET(DECKS);
    expect(srv.calls[0]!.headers.get('Authorization')).toBe('Bearer abc');
  });

  it('sends the current token on every request, not a cached one', async () => {
    const srv = fakeFetch(okResponse);
    const store = memStore({ access: 'one' });
    const api = build(srv.fetch, { tokenStore: store });
    await api.GET(DECKS);
    store.access = 'two';
    await api.GET(DECKS);
    expect(srv.calls.map((c) => c.headers.get('Authorization'))).toEqual([
      'Bearer one',
      'Bearer two',
    ]);
  });

  it.each([null, ''])('sends no Authorization header when the token is %j', async (access) => {
    const srv = fakeFetch(okResponse);
    await build(srv.fetch, { tokenStore: memStore({ access }) }).GET(DECKS);
    expect(srv.calls[0]!.headers.has('Authorization')).toBe(false);
  });

  it('sends no Authorization header when the client has no token store', async () => {
    const srv = fakeFetch(okResponse);
    await build(srv.fetch).GET(DECKS);
    expect(srv.calls[0]!.headers.has('Authorization')).toBe(false);
  });

  it("keeps a caller's own Authorization header", async () => {
    const srv = fakeFetch(okResponse);
    await build(srv.fetch, { tokenStore: memStore({ access: 'abc' }) }).GET(DECKS, {
      headers: { Authorization: 'Bearer mine' },
    });
    expect(srv.calls[0]!.headers.get('Authorization')).toBe('Bearer mine');
  });

  it('works with a store whose getAccessToken returns a Promise', async () => {
    const srv = fakeFetch(okResponse);
    const store = memStore();
    store.getAccessToken = () => Promise.resolve('async-token');
    await build(srv.fetch, { tokenStore: store }).GET(DECKS);
    expect(srv.calls[0]!.headers.get('Authorization')).toBe('Bearer async-token');
  });
});

describe('AC2: refresh once and retry once', () => {
  it('refreshes on a 401, then retries the request once with the new token', async () => {
    const srv = server();
    const store = memStore();
    const data = await build(srv.fetch, { tokenStore: store }).GET(DECKS);
    expect(data).toEqual({ ok: true });
    expect(srv.refreshCalls()).toHaveLength(1);
    const decks = srv.apiCalls();
    expect(decks).toHaveLength(2);
    expect(decks[0]!.headers.get('Authorization')).toBe('Bearer old');
    expect(decks[1]!.headers.get('Authorization')).toBe('Bearer new');
    expect(decks[1]!.url).toBe(`${BASE}${DECKS}`);
    expect(store.setTokens).toHaveBeenCalledTimes(1);
    expect(store.access).toBe('new');
  });

  it('stores the new tokens before the original request is retried', async () => {
    const order: string[] = [];
    const srv = fakeFetch((rec) => {
      if (rec.path === REFRESH_PATH) return tokenResponse('new');
      order.push(`decks:${rec.headers.get('Authorization') ?? ''}`);
      return rec.headers.get('Authorization') === 'Bearer new'
        ? okResponse()
        : errorResponse(401, 'token_expired');
    });
    const store = memStore();
    store.setTokens.mockImplementation((tokens) => {
      order.push('setTokens');
      store.access = tokens.accessToken;
    });
    await build(srv.fetch, { tokenStore: store }).GET(DECKS);
    expect(order).toEqual(['decks:Bearer old', 'setTokens', 'decks:Bearer new']);
  });

  it('keeps a POST JSON body, method and content type across the retry', async () => {
    const srv = server();
    const data = await build(srv.fetch, { tokenStore: memStore() }).POST(DECKS, {
      body: { name: 'Atraxa' },
    });
    expect(data).toEqual({ ok: true });
    const decks = srv.apiCalls();
    expect(decks).toHaveLength(2);
    for (const call of decks) {
      expect(call.method).toBe('POST');
      expect(call.headers.get('Content-Type')).toContain('application/json');
      expect(JSON.parse(call.body)).toEqual({ name: 'Atraxa' });
    }
  });

  it('keeps default and per-call headers on the retry', async () => {
    const srv = server();
    const api = createApiClient<TestPaths>({
      baseUrl: BASE,
      fetch: srv.fetch,
      headers: { 'X-Client': 'web' },
      tokenStore: memStore(),
    });
    await api.GET(DECKS, { headers: { 'X-Trace': 't1' } });
    const retry = srv.apiCalls()[1]!;
    expect(retry.headers.get('X-Client')).toBe('web');
    expect(retry.headers.get('X-Trace')).toBe('t1');
  });

  it('makes exactly 1 refresh call for 3 concurrent 401s and retries all 3', async () => {
    const srv = server();
    const api = build(srv.fetch, { tokenStore: memStore() });
    const results = await Promise.all([api.GET(DECKS), api.GET(DECKS), api.GET(DECKS)]);
    expect(results).toEqual([{ ok: true }, { ok: true }, { ok: true }]);
    expect(srv.refreshCalls()).toHaveLength(1);
    const decks = srv.apiCalls();
    expect(decks).toHaveLength(6);
    expect(decks.filter((c) => c.headers.get('Authorization') === 'Bearer old')).toHaveLength(3);
    expect(decks.filter((c) => c.headers.get('Authorization') === 'Bearer new')).toHaveLength(3);
  });

  it('rejects with the 401 when the retry also returns 401, with no second refresh', async () => {
    const srv = fakeFetch((rec) =>
      rec.path === REFRESH_PATH ? tokenResponse('new') : errorResponse(401, 'still_unauthorized'),
    );
    const onSessionExpired = vi.fn();
    const error = (await caught(
      build(srv.fetch, { tokenStore: memStore(), onSessionExpired }).GET(DECKS),
    )) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 401, code: 'still_unauthorized' });
    expect(srv.refreshCalls()).toHaveLength(1);
    expect(srv.apiCalls()).toHaveLength(2);
  });

  it('refreshes again when the token expires after a completed refresh', async () => {
    const issued = ['first', 'second'];
    let valid = 'old';
    const srv = fakeFetch((rec) => {
      if (rec.path === REFRESH_PATH) {
        valid = issued.shift() ?? 'none';
        return tokenResponse(valid);
      }
      return rec.headers.get('Authorization') === `Bearer ${valid}`
        ? okResponse()
        : errorResponse(401, 'token_expired');
    });
    const store = memStore({ access: 'old' });
    const api = build(srv.fetch, { tokenStore: store });
    await api.GET(DECKS);
    expect(srv.refreshCalls()).toHaveLength(0);

    valid = 'expired-1';
    await api.GET(DECKS);
    expect(srv.refreshCalls()).toHaveLength(1);
    expect(store.access).toBe('first');

    valid = 'expired-2';
    await api.GET(DECKS);
    expect(srv.refreshCalls()).toHaveLength(2);
    expect(store.access).toBe('second');
    expect(store.setTokens).toHaveBeenCalledTimes(2);
  });

  it('retries with the current token, without refreshing, when the store already has a newer one', async () => {
    const store = memStore({ access: 'old' });
    let first = true;
    const srv = fakeFetch((rec) => {
      if (rec.path === REFRESH_PATH) return tokenResponse('should-not-happen');
      if (first) {
        first = false;
        store.access = 'new'; // another request's refresh finished while this one was in flight
        return errorResponse(401, 'token_expired');
      }
      return rec.headers.get('Authorization') === 'Bearer new'
        ? okResponse()
        : errorResponse(401, 'token_expired');
    });
    const data = await build(srv.fetch, { tokenStore: store }).GET(DECKS);
    expect(data).toEqual({ ok: true });
    expect(srv.refreshCalls()).toHaveLength(0);
    expect(srv.apiCalls()).toHaveLength(2);
    expect(srv.apiCalls()[1]!.headers.get('Authorization')).toBe('Bearer new');
    expect(store.setTokens).not.toHaveBeenCalled();
  });

  it('still attempts a refresh on a 401 when the store has no access token', async () => {
    const srv = server();
    const store = memStore({ access: null });
    const data = await build(srv.fetch, { tokenStore: store }).GET(DECKS);
    expect(data).toEqual({ ok: true });
    expect(srv.refreshCalls()).toHaveLength(1);
    expect(srv.apiCalls()[0]!.headers.has('Authorization')).toBe(false);
    expect(srv.apiCalls()[1]!.headers.get('Authorization')).toBe('Bearer new');
  });

  it('sends the refresh and the retry through the per-call fetch override', async () => {
    const own = server();
    const other = server();
    const api = build(own.fetch, { tokenStore: memStore() });
    await api.GET(DECKS, { fetch: other.fetch as unknown as (r: Request) => Promise<Response> });
    expect(own.calls).toHaveLength(0);
    expect(other.calls.map((c) => c.path)).toEqual([DECKS, REFRESH_PATH, DECKS]);
  });

  it('does not touch a request that succeeds', async () => {
    const srv = fakeFetch(okResponse);
    const store = memStore({ access: 'abc' });
    await build(srv.fetch, { tokenStore: store }).GET(DECKS);
    expect(srv.calls).toHaveLength(1);
    expect(store.setTokens).not.toHaveBeenCalled();
  });

  it('does not refresh on non-401 errors', async () => {
    const srv = fakeFetch(() => errorResponse(403, 'forbidden'));
    const error = (await caught(
      build(srv.fetch, { tokenStore: memStore() }).GET(DECKS),
    )) as ApiError;
    expect(error).toMatchObject({ status: 403, code: 'forbidden' });
    expect(srv.calls).toHaveLength(1);
  });
});

describe('AC3: a rejected refresh', () => {
  const rejections: [string, () => Response][] = [
    ['a 401', () => errorResponse(401, 'invalid_refresh')],
    ['a 400', () => errorResponse(400, 'bad_refresh')],
    ['a 403', () => errorResponse(403, 'forbidden_refresh')],
    ['a 2xx without tokens', () => jsonResponse({ token_type: 'bearer' })],
    ['a 2xx with an empty access_token', () => tokenResponse('')],
  ];

  it.each(rejections)(
    'on %s clears the store, calls onSessionExpired once and rejects with the original 401',
    async (_label, refreshResponse) => {
      const srv = server(refreshResponse);
      const store = memStore({ refresh: 'r1' });
      const onSessionExpired = vi.fn();
      const error = (await caught(
        build(srv.fetch, { tokenStore: store, onSessionExpired }).GET(DECKS),
      )) as ApiError;
      expect(error).toBeInstanceOf(ApiError);
      expect(error).toMatchObject({ status: 401, code: 'token_expired' });
      expect(store.clear).toHaveBeenCalledTimes(1);
      expect(store.setTokens).not.toHaveBeenCalled();
      expect(onSessionExpired).toHaveBeenCalledTimes(1);
      expect(srv.refreshCalls()).toHaveLength(1);
      expect(srv.apiCalls()).toHaveLength(1); // no retry
    },
  );

  it('treats a 2xx without a refresh_token as a rejection in body mode', async () => {
    const srv = server(() => tokenResponse('new'));
    const store = memStore({ refresh: 'r1', body: true });
    const onSessionExpired = vi.fn();
    const error = (await caught(
      build(srv.fetch, { tokenStore: store, onSessionExpired }).GET(DECKS),
    )) as ApiError;
    expect(error).toMatchObject({ status: 401, code: 'token_expired' });
    expect(store.clear).toHaveBeenCalledTimes(1);
    expect(store.setTokens).not.toHaveBeenCalled();
    expect(onSessionExpired).toHaveBeenCalledTimes(1);
    expect(srv.apiCalls()).toHaveLength(1);
  });

  it('calls onSessionExpired exactly once with 3 waiting requests, all rejecting with the 401', async () => {
    const srv = server(() => errorResponse(401, 'invalid_refresh'));
    const store = memStore();
    const onSessionExpired = vi.fn();
    const api = build(srv.fetch, { tokenStore: store, onSessionExpired });
    const errors = (await Promise.all([
      caught(api.GET(DECKS)),
      caught(api.GET(DECKS)),
      caught(api.GET(DECKS)),
    ])) as ApiError[];
    for (const error of errors) {
      expect(error).toBeInstanceOf(ApiError);
      expect(error).toMatchObject({ status: 401, code: 'token_expired' });
    }
    expect(srv.refreshCalls()).toHaveLength(1);
    expect(store.clear).toHaveBeenCalledTimes(1);
    expect(onSessionExpired).toHaveBeenCalledTimes(1);
    expect(srv.apiCalls()).toHaveLength(3);
  });

  it('awaits clear() before calling onSessionExpired', async () => {
    const srv = server(() => errorResponse(401, 'invalid_refresh'));
    const order: string[] = [];
    const store = memStore();
    store.clear.mockImplementation(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10));
      order.push('clear-done');
    });
    const onSessionExpired = vi.fn(() => order.push('expired'));
    await caught(build(srv.fetch, { tokenStore: store, onSessionExpired }).GET(DECKS));
    expect(order).toEqual(['clear-done', 'expired']);
  });

  it('swallows an error thrown by onSessionExpired and still rejects with the 401', async () => {
    const srv = server(() => errorResponse(401, 'invalid_refresh'));
    const onSessionExpired = vi.fn(() => {
      throw new Error('callback exploded');
    });
    const error = (await caught(
      build(srv.fetch, { tokenStore: memStore(), onSessionExpired }).GET(DECKS),
    )) as ApiError;
    expect(error).toMatchObject({ status: 401, code: 'token_expired' });
    expect(onSessionExpired).toHaveBeenCalledTimes(1);
  });

  it('works without an onSessionExpired callback', async () => {
    const srv = server(() => errorResponse(401, 'invalid_refresh'));
    const store = memStore();
    const error = (await caught(build(srv.fetch, { tokenStore: store }).GET(DECKS))) as ApiError;
    expect(error).toMatchObject({ status: 401, code: 'token_expired' });
    expect(store.clear).toHaveBeenCalledTimes(1);
  });

  it('refreshes again for a later request after a rejection (the slot is cleared)', async () => {
    const srv = server(() => errorResponse(401, 'invalid_refresh'));
    const api = build(srv.fetch, { tokenStore: memStore() });
    await caught(api.GET(DECKS));
    await caught(api.GET(DECKS));
    expect(srv.refreshCalls()).toHaveLength(2);
  });
});

describe('AC3: a transient refresh failure', () => {
  it('leaves the store alone and rejects with network_error when the refresh cannot connect', async () => {
    const srv = fakeFetch((rec) => {
      if (rec.path === REFRESH_PATH) throw new TypeError('Failed to fetch');
      return errorResponse(401, 'token_expired');
    });
    const store = memStore({ refresh: 'r1' });
    const onSessionExpired = vi.fn();
    const error = (await caught(
      build(srv.fetch, { tokenStore: store, onSessionExpired }).GET(DECKS),
    )) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 0, code: 'network_error' });
    expect(store.clear).not.toHaveBeenCalled();
    expect(store.setTokens).not.toHaveBeenCalled();
    expect(store.access).toBe('old');
    expect(onSessionExpired).not.toHaveBeenCalled();
    expect(srv.apiCalls()).toHaveLength(1);
  });

  it.each([
    [500, 'server_error'],
    [503, 'unavailable'],
    [408, 'timeout'],
    [429, 'rate_limited'],
  ])('leaves the store alone and rejects with the refresh error for a %i', async (status, code) => {
    const srv = server(() => errorResponse(status, code));
    const store = memStore();
    const onSessionExpired = vi.fn();
    const error = (await caught(
      build(srv.fetch, { tokenStore: store, onSessionExpired }).GET(DECKS),
    )) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status, code });
    expect(store.clear).not.toHaveBeenCalled();
    expect(onSessionExpired).not.toHaveBeenCalled();
    expect(srv.apiCalls()).toHaveLength(1);
  });

  it('rejects all 3 waiting requests with the refresh error after one refresh', async () => {
    const srv = server(() => errorResponse(500, 'server_error'));
    const store = memStore();
    const onSessionExpired = vi.fn();
    const api = build(srv.fetch, { tokenStore: store, onSessionExpired });
    const errors = (await Promise.all([
      caught(api.GET(DECKS)),
      caught(api.GET(DECKS)),
      caught(api.GET(DECKS)),
    ])) as ApiError[];
    for (const error of errors) expect(error).toMatchObject({ status: 500, code: 'server_error' });
    expect(srv.refreshCalls()).toHaveLength(1);
    expect(store.clear).not.toHaveBeenCalled();
    expect(onSessionExpired).not.toHaveBeenCalled();
  });

  it('can refresh again later after a transient failure', async () => {
    let fail = true;
    const srv = server(() => (fail ? errorResponse(500, 'server_error') : tokenResponse('new')));
    const api = build(srv.fetch, { tokenStore: memStore() });
    await caught(api.GET(DECKS));
    fail = false;
    expect(await api.GET(DECKS)).toEqual({ ok: true });
    expect(srv.refreshCalls()).toHaveLength(2);
  });
});

describe('AC4: cookie mode', () => {
  const cookieStores: [string, () => Store][] = [
    ['no getRefreshToken method', () => memStore()],
    [
      'getRefreshToken returning null',
      () => {
        const store = memStore();
        store.getRefreshToken = () => null;
        return store;
      },
    ],
    [
      'getRefreshToken returning an empty string',
      () => {
        const store = memStore();
        store.getRefreshToken = () => '';
        return store;
      },
    ],
  ];

  it.each(cookieStores)(
    'POSTs the refresh with credentials include, no body and no Authorization (%s)',
    async (_label, makeStore) => {
      const srv = server();
      const store = makeStore();
      await build(srv.fetch, { tokenStore: store }).GET(DECKS);
      const refresh = srv.refreshCalls();
      expect(refresh).toHaveLength(1);
      expect(refresh[0]!.method).toBe('POST');
      expect(refresh[0]!.url).toBe(`${BASE}${REFRESH_PATH}`);
      expect(refresh[0]!.credentials).toBe('include');
      expect(refresh[0]!.body).toBe('');
      expect(refresh[0]!.headers.has('Authorization')).toBe(false);
      expect(refresh[0]!.headers.has('Content-Type')).toBe(false);
      expect(refresh[0]!.headers.has('X-Client')).toBe(false);
    },
  );

  it('calls setTokens with the access token only, ignoring a body refresh_token', async () => {
    const srv = server(() => tokenResponse('new', 'ignored-refresh'));
    const store = memStore();
    await build(srv.fetch, { tokenStore: store }).GET(DECKS);
    expect(store.setTokens).toHaveBeenCalledTimes(1);
    const tokens = store.setTokens.mock.calls[0]![0];
    expect(tokens.accessToken).toBe('new');
    expect(tokens.refreshToken).toBeUndefined();
  });
});

describe('AC4: body mode', () => {
  it('sends {refresh_token} as JSON with X-Client: mobile and no Authorization', async () => {
    const srv = server(() => tokenResponse('new', 'r2'));
    const store = memStore({ refresh: 'r1', body: true });
    await build(srv.fetch, { tokenStore: store }).GET(DECKS);
    const refresh = srv.refreshCalls();
    expect(refresh).toHaveLength(1);
    expect(refresh[0]!.method).toBe('POST');
    expect(refresh[0]!.url).toBe(`${BASE}${REFRESH_PATH}`);
    expect(refresh[0]!.headers.get('X-Client')).toBe('mobile');
    expect(refresh[0]!.headers.get('Content-Type')).toContain('application/json');
    expect(JSON.parse(refresh[0]!.body)).toEqual({ refresh_token: 'r1' }); // secrets-check: allow
    expect(refresh[0]!.headers.has('Authorization')).toBe(false);
  });

  it('calls setTokens with both tokens and retries with the new access token', async () => {
    const srv = server(() => tokenResponse('new', 'r2'));
    const store = memStore({ refresh: 'r1', body: true });
    await build(srv.fetch, { tokenStore: store }).GET(DECKS);
    expect(store.setTokens).toHaveBeenCalledTimes(1);
    expect(store.setTokens.mock.calls[0]![0]).toEqual({ accessToken: 'new', refreshToken: 'r2' });
    expect(store.refresh).toBe('r2');
    expect(srv.apiCalls()[1]!.headers.get('Authorization')).toBe('Bearer new');
  });

  it('decides the mode per refresh', async () => {
    const srv = server((rec) => (rec.body ? tokenResponse('new', 'r2') : tokenResponse('new')));
    const store = memStore({ refresh: 'r1', body: true });
    const api = build(srv.fetch, { tokenStore: store });
    await api.GET(DECKS);
    store.refresh = null; // e.g. the mobile store lost its refresh token: now cookie mode
    store.access = 'old';
    await api.GET(DECKS);
    const refresh = srv.refreshCalls();
    expect(refresh).toHaveLength(2);
    expect(refresh[0]!.body).not.toBe('');
    expect(refresh[1]!.body).toBe('');
    expect(refresh[1]!.credentials).toBe('include');
  });
});

describe('AC4: async token stores', () => {
  it('supports Promise-returning methods in cookie mode', async () => {
    const srv = server();
    const state = { access: 'old' as string | null };
    const setTokens = vi.fn(async (tokens: { accessToken: string }) => {
      await Promise.resolve();
      state.access = tokens.accessToken;
    });
    const clear = vi.fn(async () => {
      await Promise.resolve();
      state.access = null;
    });
    const store: TokenStore = {
      getAccessToken: () => Promise.resolve(state.access),
      setTokens,
      clear,
    };
    const data = await build(srv.fetch, { tokenStore: store }).GET(DECKS);
    expect(data).toEqual({ ok: true });
    expect(setTokens).toHaveBeenCalledWith({ accessToken: 'new' });
    expect(srv.apiCalls()[1]!.headers.get('Authorization')).toBe('Bearer new');
  });

  it('supports Promise-returning methods in body mode and awaits setTokens before the retry', async () => {
    const srv = server(() => tokenResponse('new', 'r2'));
    const state = { access: 'old' as string | null, refresh: 'r1' as string | null };
    const store: TokenStore = {
      getAccessToken: () => Promise.resolve(state.access),
      getRefreshToken: () => Promise.resolve(state.refresh),
      setTokens: async (tokens) => {
        await new Promise((resolve) => setTimeout(resolve, 10));
        state.access = tokens.accessToken;
        state.refresh = tokens.refreshToken ?? null;
      },
      clear: async () => {
        await Promise.resolve();
        state.access = null;
        state.refresh = null;
      },
    };
    await build(srv.fetch, { tokenStore: store }).GET(DECKS);
    expect(JSON.parse(srv.refreshCalls()[0]!.body)).toEqual({ refresh_token: 'r1' }); // secrets-check: allow
    expect(state).toEqual({ access: 'new', refresh: 'r2' });
    expect(srv.apiCalls()[1]!.headers.get('Authorization')).toBe('Bearer new');
  });
});

describe('decision 4: auth paths send credentials', () => {
  it.each(['/api/v1/auth/login', '/api/v1/auth/logout', '/api/v1/auth/logout-all'] as const)(
    '%s is sent with credentials include',
    async (path) => {
      const srv = fakeFetch(okResponse);
      await build(srv.fetch).POST(path);
      expect(srv.calls[0]!.credentials).toBe('include');
    },
  );

  it('sends credentials include on auth paths with a token store too', async () => {
    const srv = fakeFetch(okResponse);
    await build(srv.fetch, { tokenStore: memStore() }).POST('/api/v1/auth/login');
    expect(srv.calls[0]!.credentials).toBe('include');
  });

  it('does not force credentials include on other paths', async () => {
    const srv = fakeFetch(okResponse);
    await build(srv.fetch, { tokenStore: memStore() }).GET(DECKS);
    expect(srv.calls[0]!.credentials).not.toBe('include');
  });

  it('respects credentials set explicitly by the caller', async () => {
    const srv = fakeFetch(okResponse);
    await build(srv.fetch).POST('/api/v1/auth/login', { credentials: 'omit' });
    expect(srv.calls[0]!.credentials).toBe('omit');
  });
});

describe('decision 5: which 401s refresh', () => {
  it.each(['/api/v1/auth/login', '/api/v1/auth/logout'] as const)(
    'a 401 from %s rejects without refreshing',
    async (path) => {
      const srv = fakeFetch(() => errorResponse(401, 'bad_credentials'));
      const store = memStore();
      const onSessionExpired = vi.fn();
      const error = (await caught(
        build(srv.fetch, { tokenStore: store, onSessionExpired }).POST(path),
      )) as ApiError;
      expect(error).toMatchObject({ status: 401, code: 'bad_credentials' });
      expect(srv.calls).toHaveLength(1);
      expect(store.clear).not.toHaveBeenCalled();
      expect(onSessionExpired).not.toHaveBeenCalled();
    },
  );

  it('a 401 from the refresh path itself rejects without refreshing', async () => {
    const srv = fakeFetch(() => errorResponse(401, 'invalid_refresh'));
    const error = (await caught(
      build(srv.fetch, { tokenStore: memStore() }).POST('/api/v1/auth/refresh'),
    )) as ApiError;
    expect(error).toMatchObject({ status: 401, code: 'invalid_refresh' });
    expect(srv.calls).toHaveLength(1);
  });

  it('a 401 from logout-all does refresh and retry', async () => {
    const srv = fakeFetch((rec) => {
      if (rec.path === REFRESH_PATH) return tokenResponse('new');
      return rec.headers.get('Authorization') === 'Bearer new'
        ? okResponse()
        : errorResponse(401, 'token_expired');
    });
    const data = await build(srv.fetch, { tokenStore: memStore() }).POST('/api/v1/auth/logout-all');
    expect(data).toEqual({ ok: true });
    expect(srv.refreshCalls()).toHaveLength(1);
    expect(srv.apiCalls('/api/v1/auth/logout-all')).toHaveLength(2);
  });
});

describe('decision 6: navigator.locks in cookie mode', () => {
  function stubLocks() {
    const state = { inside: false, names: [] as string[], fetchedInside: [] as boolean[] };
    const request = vi.fn(async (name: string, ...rest: unknown[]) => {
      const callback = rest[rest.length - 1] as () => Promise<unknown>;
      state.names.push(name);
      state.inside = true;
      try {
        return await callback();
      } finally {
        state.inside = false;
      }
    });
    vi.stubGlobal('navigator', { locks: { request } });
    return { state, request };
  }

  it("runs the cookie-mode refresh inside locks.request('mtgstats-auth-refresh')", async () => {
    const { state, request } = stubLocks();
    const srv = server((rec) => {
      state.fetchedInside.push(state.inside);
      expect(rec.path).toBe(REFRESH_PATH);
      return tokenResponse('new');
    });
    const data = await build(srv.fetch, { tokenStore: memStore() }).GET(DECKS);
    expect(data).toEqual({ ok: true });
    expect(request).toHaveBeenCalledTimes(1);
    expect(state.names).toEqual(['mtgstats-auth-refresh']);
    expect(state.fetchedInside).toEqual([true]);
  });

  it('takes the lock once for 3 concurrent 401s', async () => {
    const { request } = stubLocks();
    const srv = server();
    const api = build(srv.fetch, { tokenStore: memStore() });
    await Promise.all([api.GET(DECKS), api.GET(DECKS), api.GET(DECKS)]);
    expect(request).toHaveBeenCalledTimes(1);
    expect(srv.refreshCalls()).toHaveLength(1);
  });

  it('does not use the lock in body mode', async () => {
    const { request } = stubLocks();
    const srv = server(() => tokenResponse('new', 'r2'));
    await build(srv.fetch, { tokenStore: memStore({ refresh: 'r1', body: true }) }).GET(DECKS);
    expect(request).not.toHaveBeenCalled();
    expect(srv.refreshCalls()).toHaveLength(1);
  });

  it('refreshes directly when navigator.locks is unavailable', async () => {
    vi.stubGlobal('navigator', {});
    const srv = server();
    const data = await build(srv.fetch, { tokenStore: memStore() }).GET(DECKS);
    expect(data).toEqual({ ok: true });
    expect(srv.refreshCalls()).toHaveLength(1);
  });
});

describe('decision 7: no token store', () => {
  it('rejects a 401 as before: no header, no refresh, one call', async () => {
    const srv = fakeFetch(() => errorResponse(401, 'token_expired'));
    const error = (await caught(build(srv.fetch).GET(DECKS))) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 401, code: 'token_expired' });
    expect(srv.calls).toHaveLength(1);
    expect(srv.calls[0]!.headers.has('Authorization')).toBe(false);
  });

  it('does not call onSessionExpired when there is nothing to refresh', async () => {
    const srv = fakeFetch(() => errorResponse(401, 'token_expired'));
    const onSessionExpired = vi.fn();
    await caught(build(srv.fetch, { onSessionExpired }).GET(DECKS));
    expect(onSessionExpired).not.toHaveBeenCalled();
  });
});

describe('existing guarantees with a token store', () => {
  it('still wraps a network error on the original request as network_error', async () => {
    const srv = fakeFetch(() => {
      throw new TypeError('Failed to fetch');
    });
    const error = (await caught(
      build(srv.fetch, { tokenStore: memStore() }).GET(DECKS),
    )) as ApiError;
    expect(error).toMatchObject({ status: 0, code: 'network_error' });
  });

  it('still rethrows an AbortError unchanged', async () => {
    const abort = new DOMException('aborted', 'AbortError');
    const srv = fakeFetch(() => {
      throw abort;
    });
    const error = await caught(build(srv.fetch, { tokenStore: memStore() }).GET(DECKS));
    expect(error).toBe(abort);
  });

  it('still maps a non-JSON 2xx to unexpected_response with the real status', async () => {
    const srv = fakeFetch(() => new Response('<html>', { status: 200 }));
    const error = (await caught(
      build(srv.fetch, { tokenStore: memStore() }).GET(DECKS),
    )) as ApiError;
    expect(error).toMatchObject({ status: 200, code: 'unexpected_response' });
  });
});
