import { describe, expect, it, vi } from 'vitest';
import { createApiClient } from './client';
import type { TokenStore } from './auth';

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

describe('auth middleware', () => {
  it('refreshes once for concurrent 401s and retries each request with the new token', async () => {
    let access = 'old';
    const store: TokenStore = {
      getAccessToken: () => access,
      setTokens: (t) => {
        access = t.accessToken;
      },
      clear: vi.fn(),
    };
    let refreshes = 0;
    const fetchMock = vi.fn(async (req: Request) => {
      if (new URL(req.url).pathname === '/api/v1/auth/refresh') {
        refreshes += 1;
        return json(200, { access_token: 'new', token_type: 'bearer', expires_in: 900 }); // secrets-check: allow
      }
      return req.headers.get('Authorization') === 'Bearer new'
        ? json(200, { status: 'ok' })
        : json(401, { error: { code: 'token_expired', message: 'x', details: null } });
    });
    const client = createApiClient({
      baseUrl: 'http://x',
      fetch: fetchMock as unknown as typeof fetch,
      tokenStore: store,
    });
    const results = await Promise.all([
      client.GET('/api/v1/health/live'),
      client.GET('/api/v1/health/live'),
      client.GET('/api/v1/health/live'),
    ]);
    expect(results).toHaveLength(3);
    expect(refreshes).toBe(1);
  });

  it('clears the store and signals expiry when the refresh is rejected', async () => {
    const clear = vi.fn();
    const onSessionExpired = vi.fn();
    const fetchMock = vi.fn(async (req: Request) =>
      new URL(req.url).pathname === '/api/v1/auth/refresh'
        ? json(401, { error: { code: 'invalid_token', message: 'x', details: null } })
        : json(401, { error: { code: 'token_expired', message: 'x', details: null } }),
    );
    const client = createApiClient({
      baseUrl: 'http://x',
      fetch: fetchMock as unknown as typeof fetch,
      tokenStore: { getAccessToken: () => 'old', setTokens: vi.fn(), clear },
      onSessionExpired,
    });
    await expect(client.GET('/api/v1/health/live')).rejects.toMatchObject({
      status: 401,
      code: 'token_expired',
    });
    expect(clear).toHaveBeenCalledTimes(1);
    expect(onSessionExpired).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
