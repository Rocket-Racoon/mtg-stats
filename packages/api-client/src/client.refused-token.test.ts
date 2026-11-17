// S-2.1.12: when the token store refuses the token a refresh just returned (a sign-out happened
// meanwhile), the session is over: the original request is NOT retried and the outcome is a rejection.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, createApiClient, REFRESH_PATH } from './index';
import type { TokenStore } from './index';

interface Ok {
  headers: Record<string, unknown>;
  content: { 'application/json': { ok: boolean } };
}
interface TestPaths {
  '/api/v1/decks': {
    get: {
      parameters: { query?: never; header?: never; path?: never };
      requestBody?: never;
      responses: { 200: Ok };
    };
  };
}

const BASE = 'https://api.test';
const DECKS = '/api/v1/decks';

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

function setup(opts: { refuse: boolean }) {
  const paths: string[] = [];
  const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input, init);
    const path = new URL(request.url).pathname;
    paths.push(path);
    if (path === REFRESH_PATH) {
      return json({ access_token: 'new-access', token_type: 'bearer', expires_in: 900 }); // secrets-check: allow
    }
    return request.headers.get('Authorization') === 'Bearer new-access'
      ? json({ ok: true })
      : json(
          { error: { code: 'token_expired', message: 'x', details: null, request_id: 'r' } },
          401,
        );
  });
  let access: string | null = 'old';
  const store: TokenStore & {
    setTokens: ReturnType<typeof vi.fn<TokenStore['setTokens']>>;
    clear: ReturnType<typeof vi.fn<TokenStore['clear']>>;
  } = {
    getAccessToken: () => access,
    setTokens: vi.fn<TokenStore['setTokens']>((t) => {
      if (!opts.refuse) access = t.accessToken; // a signed-out store ignores the new token
    }),
    clear: vi.fn<TokenStore['clear']>(() => {
      access = null;
    }),
  };
  const onSessionExpired = vi.fn();
  const client = createApiClient<TestPaths>({
    baseUrl: BASE,
    fetch: fetchImpl as unknown as typeof fetch,
    tokenStore: store,
    onSessionExpired,
  });
  return {
    client,
    store,
    onSessionExpired,
    count: (p: string) => paths.filter((x) => x === p).length,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('a refreshed token the store refuses', () => {
  it('does not retry the original request, treats it as rejected and rejects with the 401', async () => {
    const s = setup({ refuse: true });
    const error = await s.client.GET(DECKS).then(
      () => null,
      (e: unknown) => e,
    );
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 401, code: 'token_expired' });
    expect(s.count(DECKS)).toBe(1); // no retry
    expect(s.count(REFRESH_PATH)).toBe(1);
    expect(s.store.clear).toHaveBeenCalledTimes(1);
    expect(s.onSessionExpired).toHaveBeenCalledTimes(1);
  });

  it('refreshSession() resolves false when the store refuses the token', async () => {
    const s = setup({ refuse: true });
    await expect(s.client.refreshSession()).resolves.toBe(false);
    expect(s.onSessionExpired).toHaveBeenCalledTimes(1);
  });

  it('still refreshes and retries once when the store accepts the token', async () => {
    const s = setup({ refuse: false });
    const result = await s.client.GET(DECKS);
    expect(result).toEqual({ ok: true });
    expect(s.count(REFRESH_PATH)).toBe(1);
    expect(s.count(DECKS)).toBe(2);
    expect(s.store.clear).not.toHaveBeenCalled();
    expect(s.onSessionExpired).not.toHaveBeenCalled();
  });
});
