// S-2.1.6 decision 3: ApiClient.refreshSession() runs the same single-flight refresh (and the Web
// Locks path in cookie mode) as the 401 retry. true after a refresh, false on a rejection (with the
// usual clear() + onSessionExpired side effects), throws the ApiError on a transient failure.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, createApiClient, REFRESH_PATH } from './index';
import type { TokenStore } from './index';

const BASE = 'https://api.test';
const LIVE = '/api/v1/health/live';

interface Rec {
  path: string;
  method: string;
  headers: Headers;
  credentials: RequestCredentials;
  body: string;
}

function json(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init.headers },
  });
}
const failure = (status: number, code: string, headers: Record<string, string> = {}) =>
  json(
    { error: { code, message: `${code} message`, details: null, request_id: 'r' } },
    { status, headers },
  );
const tokens = (access: string, refresh?: string) =>
  json({
    access_token: access, // secrets-check: allow
    token_type: 'bearer',
    expires_in: 900,
    ...(refresh === undefined ? {} : { refresh_token: refresh }), // secrets-check: allow
  });

function fakeFetch(handler: (rec: Rec) => Response | Promise<Response>) {
  const calls: Rec[] = [];
  const impl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input, init);
    const rec: Rec = {
      path: new URL(request.url).pathname,
      method: request.method,
      headers: request.headers,
      credentials: request.credentials,
      body: await request.text(),
    };
    calls.push(rec);
    return handler(rec);
  });
  return {
    fetch: impl as unknown as typeof fetch,
    calls,
    refreshCalls: () => calls.filter((c) => c.path === REFRESH_PATH),
  };
}

interface Store extends TokenStore {
  access: string | null;
  refresh: string | null;
  setTokens: ReturnType<typeof vi.fn<TokenStore['setTokens']>>;
  clear: ReturnType<typeof vi.fn<TokenStore['clear']>>;
}

function memStore(init: { access?: string | null; body?: boolean } = {}): Store {
  const store: Store = {
    access: init.access === undefined ? null : init.access,
    refresh: init.body ? 'stored-refresh' : null, // secrets-check: allow
    getAccessToken: () => store.access,
    setTokens: vi.fn<TokenStore['setTokens']>((t) => {
      store.access = t.accessToken;
      if (t.refreshToken !== undefined) store.refresh = t.refreshToken;
    }),
    clear: vi.fn<TokenStore['clear']>(() => {
      store.access = null;
      store.refresh = null;
    }),
  };
  if (init.body) store.getRefreshToken = () => store.refresh;
  return store;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('refreshSession: success', () => {
  it('resolves true with setTokens already called', async () => {
    const srv = fakeFetch(() => tokens('fresh'));
    const store = memStore();
    const api = createApiClient({ baseUrl: BASE, fetch: srv.fetch, tokenStore: store });
    await expect(api.refreshSession()).resolves.toBe(true);
    expect(store.setTokens).toHaveBeenCalledTimes(1);
    expect(store.setTokens).toHaveBeenCalledWith({ accessToken: 'fresh' });
    expect(store.access).toBe('fresh');
    expect(store.clear).not.toHaveBeenCalled();
  });

  it('in cookie mode POSTs to the refresh path with credentials include and no body', async () => {
    const srv = fakeFetch(() => tokens('fresh'));
    const api = createApiClient({ baseUrl: BASE, fetch: srv.fetch, tokenStore: memStore() });
    await api.refreshSession();
    expect(srv.calls).toHaveLength(1);
    expect(srv.calls[0]!.path).toBe('/api/v1/auth/refresh');
    expect(srv.calls[0]!.method).toBe('POST');
    expect(srv.calls[0]!.credentials).toBe('include');
    expect(srv.calls[0]!.body).toBe('');
  });

  it('in body mode sends the stored refresh token and stores the rotated pair', async () => {
    const srv = fakeFetch(() => tokens('fresh', 'rotated'));
    const store = memStore({ body: true });
    const api = createApiClient({ baseUrl: BASE, fetch: srv.fetch, tokenStore: store });
    await expect(api.refreshSession()).resolves.toBe(true);
    expect(JSON.parse(srv.calls[0]!.body)).toEqual({ refresh_token: 'stored-refresh' }); // secrets-check: allow
    expect(store.setTokens).toHaveBeenCalledWith({
      accessToken: 'fresh',
      refreshToken: 'rotated',
    });
  });

  it('lets the next request carry the new Bearer token', async () => {
    const srv = fakeFetch((rec) => (rec.path === REFRESH_PATH ? tokens('fresh') : json({})));
    const api = createApiClient({ baseUrl: BASE, fetch: srv.fetch, tokenStore: memStore() });
    await api.refreshSession();
    await api.GET(LIVE);
    expect(srv.calls[1]!.headers.get('Authorization')).toBe('Bearer fresh');
  });
});

describe('refreshSession: rejection', () => {
  it.each([401, 400, 403])(
    'resolves false on a %d, clears the store and calls onSessionExpired once',
    async (status) => {
      const srv = fakeFetch(() => failure(status, 'invalid_refresh'));
      const store = memStore({ access: 'stale' });
      const onSessionExpired = vi.fn();
      const api = createApiClient({
        baseUrl: BASE,
        fetch: srv.fetch,
        tokenStore: store,
        onSessionExpired,
      });
      await expect(api.refreshSession()).resolves.toBe(false);
      expect(store.clear).toHaveBeenCalledTimes(1);
      expect(store.access).toBeNull();
      expect(onSessionExpired).toHaveBeenCalledTimes(1);
      expect(store.setTokens).not.toHaveBeenCalled();
    },
  );

  it('runs clear() before onSessionExpired', async () => {
    const order: string[] = [];
    const store = memStore();
    store.clear.mockImplementation(() => {
      order.push('clear');
    });
    const api = createApiClient({
      baseUrl: BASE,
      fetch: fakeFetch(() => failure(401, 'invalid_refresh')).fetch,
      tokenStore: store,
      onSessionExpired: () => order.push('expired'),
    });
    await api.refreshSession();
    expect(order).toEqual(['clear', 'expired']);
  });

  it('resolves false when a 200 has no access_token', async () => {
    const store = memStore();
    const api = createApiClient({
      baseUrl: BASE,
      fetch: fakeFetch(() => json({ token_type: 'bearer' })).fetch,
      tokenStore: store,
    });
    await expect(api.refreshSession()).resolves.toBe(false);
    expect(store.setTokens).not.toHaveBeenCalled();
  });
});

describe('refreshSession: transient failure', () => {
  async function caught(promise: Promise<unknown>): Promise<unknown> {
    try {
      await promise;
    } catch (error) {
      return error;
    }
    throw new Error('expected refreshSession to throw');
  }

  it('throws the ApiError on a 503 and leaves the session alone', async () => {
    const store = memStore({ access: 'keep' });
    const onSessionExpired = vi.fn();
    const api = createApiClient({
      baseUrl: BASE,
      fetch: fakeFetch(() => failure(503, 'service_unavailable')).fetch,
      tokenStore: store,
      onSessionExpired,
    });
    const error = await caught(api.refreshSession());
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(503);
    expect((error as ApiError).code).toBe('service_unavailable');
    expect(store.clear).not.toHaveBeenCalled();
    expect(onSessionExpired).not.toHaveBeenCalled();
    expect(store.access).toBe('keep');
  });

  it('throws a network_error ApiError when fetch rejects', async () => {
    const onSessionExpired = vi.fn();
    const failing = vi.fn(() => {
      throw new TypeError('Failed to fetch');
    }) as unknown as typeof fetch;
    const api = createApiClient({
      baseUrl: BASE,
      fetch: failing,
      tokenStore: memStore(),
      onSessionExpired,
    });
    const error = await caught(api.refreshSession());
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(0);
    expect((error as ApiError).code).toBe('network_error');
    expect(onSessionExpired).not.toHaveBeenCalled();
  });

  it('throws on a 429 and carries retryAfter', async () => {
    const api = createApiClient({
      baseUrl: BASE,
      fetch: fakeFetch(() => failure(429, 'rate_limited', { 'Retry-After': '60' })).fetch,
      tokenStore: memStore(),
    });
    const error = await caught(api.refreshSession());
    expect((error as ApiError).status).toBe(429);
    expect((error as ApiError).retryAfter).toBe(60);
  });
});

describe('refreshSession: no token store', () => {
  it('resolves false without calling fetch', async () => {
    const srv = fakeFetch(() => tokens('fresh'));
    const api = createApiClient({ baseUrl: BASE, fetch: srv.fetch });
    await expect(api.refreshSession()).resolves.toBe(false);
    expect(srv.calls).toHaveLength(0);
  });
});

describe('refreshSession: single flight', () => {
  const slow = (rec: Rec): Promise<Response> =>
    new Promise((resolve) => {
      setTimeout(() => {
        resolve(rec.path === REFRESH_PATH ? tokens('new') : json({}));
      }, 10);
    });

  it('two concurrent calls share one refresh request', async () => {
    const srv = fakeFetch(slow);
    const api = createApiClient({ baseUrl: BASE, fetch: srv.fetch, tokenStore: memStore() });
    const results = await Promise.all([api.refreshSession(), api.refreshSession()]);
    expect(results).toEqual([true, true]);
    expect(srv.refreshCalls()).toHaveLength(1);
  });

  it('shares the refresh with a concurrent 401 retry', async () => {
    const srv = fakeFetch((rec) => {
      if (rec.path === REFRESH_PATH) return slow(rec);
      const auth = rec.headers.get('Authorization') ?? '';
      return auth === 'Bearer new' ? json({ status: 'ok' }) : failure(401, 'token_expired');
    });
    const store = memStore({ access: 'old' });
    const api = createApiClient({ baseUrl: BASE, fetch: srv.fetch, tokenStore: store });
    const [data, ok] = await Promise.all([api.GET(LIVE), api.refreshSession()]);
    expect(ok).toBe(true);
    expect(data).toEqual({ status: 'ok' });
    expect(srv.refreshCalls()).toHaveLength(1);
  });

  it('a rejection reaches every waiter, with the side effects once', async () => {
    const srv = fakeFetch(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10));
      return failure(401, 'invalid_refresh');
    });
    const store = memStore();
    const onSessionExpired = vi.fn();
    const api = createApiClient({
      baseUrl: BASE,
      fetch: srv.fetch,
      tokenStore: store,
      onSessionExpired,
    });
    const results = await Promise.all([api.refreshSession(), api.refreshSession()]);
    expect(results).toEqual([false, false]);
    expect(srv.refreshCalls()).toHaveLength(1);
    expect(store.clear).toHaveBeenCalledTimes(1);
    expect(onSessionExpired).toHaveBeenCalledTimes(1);
  });

  it('a later call after the first settles refreshes again', async () => {
    const srv = fakeFetch(() => tokens('new'));
    const api = createApiClient({ baseUrl: BASE, fetch: srv.fetch, tokenStore: memStore() });
    await api.refreshSession();
    await api.refreshSession();
    expect(srv.refreshCalls()).toHaveLength(2);
  });
});

describe('refreshSession: Web Locks in cookie mode', () => {
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

  it("runs the refresh inside locks.request('mtgstats-auth-refresh')", async () => {
    const { state, request } = stubLocks();
    const srv = fakeFetch(() => {
      state.fetchedInside.push(state.inside);
      return tokens('new');
    });
    const api = createApiClient({ baseUrl: BASE, fetch: srv.fetch, tokenStore: memStore() });
    await expect(api.refreshSession()).resolves.toBe(true);
    expect(request).toHaveBeenCalledTimes(1);
    expect(state.names).toEqual(['mtgstats-auth-refresh']);
    expect(state.fetchedInside).toEqual([true]);
  });

  it('does not take the lock in body mode', async () => {
    const { request } = stubLocks();
    const api = createApiClient({
      baseUrl: BASE,
      fetch: fakeFetch(() => tokens('new', 'r2')).fetch,
      tokenStore: memStore({ body: true }),
    });
    await api.refreshSession();
    expect(request).not.toHaveBeenCalled();
  });

  it('refreshes directly when navigator.locks is unavailable', async () => {
    vi.stubGlobal('navigator', {});
    const srv = fakeFetch(() => tokens('new'));
    const api = createApiClient({ baseUrl: BASE, fetch: srv.fetch, tokenStore: memStore() });
    await expect(api.refreshSession()).resolves.toBe(true);
    expect(srv.refreshCalls()).toHaveLength(1);
  });
});
