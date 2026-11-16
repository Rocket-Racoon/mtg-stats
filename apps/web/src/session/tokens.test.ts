// S-1.5.3 / decision B3: on web the access token lives in memory and the refresh token is the
// HttpOnly cookie, so the store has no getRefreshToken (cookie mode).
import { afterEach, describe, expect, it, vi } from 'vitest';
import { REFRESH_PATH } from '@mtg-stats/api-client';
import { api } from '../api';
import { memoryTokenStore } from './tokens';
import { useSessionStore } from './store';

afterEach(async () => {
  await memoryTokenStore.clear();
  useSessionStore.getState().setStatus('signed_out');
  vi.unstubAllGlobals();
});

describe('memoryTokenStore', () => {
  it('has no access token initially', async () => {
    await memoryTokenStore.clear();
    expect(await memoryTokenStore.getAccessToken()).toBeNull();
  });

  it('has no getRefreshToken, so the client uses cookie mode', () => {
    expect(memoryTokenStore.getRefreshToken).toBeUndefined();
  });

  it('keeps the access token set by setTokens', async () => {
    await memoryTokenStore.setTokens({ accessToken: 'abc' });
    expect(await memoryTokenStore.getAccessToken()).toBe('abc');
  });

  it('replaces the access token on a later setTokens', async () => {
    await memoryTokenStore.setTokens({ accessToken: 'one' });
    await memoryTokenStore.setTokens({ accessToken: 'two' });
    expect(await memoryTokenStore.getAccessToken()).toBe('two');
  });

  it('clear drops the access token', async () => {
    await memoryTokenStore.setTokens({ accessToken: 'abc' });
    await memoryTokenStore.clear();
    expect(await memoryTokenStore.getAccessToken()).toBeNull();
  });
});

describe('the app api client', () => {
  function stubFetch(handler: (url: string, headers: Headers) => Response) {
    const calls: { url: string; headers: Headers; credentials: RequestCredentials }[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const request = new Request(input, init);
        calls.push({
          url: request.url,
          headers: request.headers,
          credentials: request.credentials,
        });
        return handler(request.url, request.headers);
      }),
    );
    return calls;
  }

  const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    });
  const unauthorized = (code: string) =>
    json({ error: { code, message: code, details: null, request_id: 'r' } }, 401);

  it('sends the in-memory access token as a Bearer header', async () => {
    await memoryTokenStore.setTokens({ accessToken: 'abc' });
    const calls = stubFetch(() => json({ status: 'ok' }));
    await api.GET('/api/v1/health/live');
    expect(calls[0]!.headers.get('Authorization')).toBe('Bearer abc');
  });

  it('sets the session store to signed_out when the refresh is rejected', async () => {
    await memoryTokenStore.setTokens({ accessToken: 'expired' });
    useSessionStore.getState().setStatus('signed_in');
    const calls = stubFetch((url) =>
      unauthorized(url.endsWith(REFRESH_PATH) ? 'invalid_refresh' : 'token_expired'),
    );
    await expect(api.GET('/api/v1/health/live')).rejects.toMatchObject({
      status: 401,
      code: 'token_expired',
    });
    expect(calls.filter((c) => c.url.endsWith(REFRESH_PATH))).toHaveLength(1);
    expect(calls.find((c) => c.url.endsWith(REFRESH_PATH))!.credentials).toBe('include');
    expect(useSessionStore.getState().status).toBe('signed_out');
    expect(await memoryTokenStore.getAccessToken()).toBeNull();
  });

  it('keeps the session when the refresh succeeds', async () => {
    await memoryTokenStore.setTokens({ accessToken: 'expired' });
    useSessionStore.getState().setStatus('signed_in');
    stubFetch((url, headers) => {
      if (url.endsWith(REFRESH_PATH)) {
        return json({ access_token: 'fresh', token_type: 'bearer', expires_in: 900 }); // secrets-check: allow
      }
      return headers.get('Authorization') === 'Bearer fresh'
        ? json({ status: 'ok' })
        : unauthorized('token_expired');
    });
    await api.GET('/api/v1/health/live');
    expect(useSessionStore.getState().status).toBe('signed_in');
    expect(await memoryTokenStore.getAccessToken()).toBe('fresh');
  });
});
