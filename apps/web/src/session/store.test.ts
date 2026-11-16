// S-2.1.6 AC5 and AC6: the in-memory session store, the token-store adapter over it and the app
// api client wired to it (decision 4).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { REFRESH_PATH } from '@mtg-stats/api-client';
import { api } from '../api';
import { memoryTokenStore } from './tokens';
import { useSessionStore } from './store';

beforeEach(() => {
  useSessionStore.setState({ status: 'signed_out', accessToken: null });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
const unauthorized = (code: string) =>
  json({ error: { code, message: code, details: null, request_id: 'r' } }, 401);

describe('session store', () => {
  it('starts as restoring with no access token on a fresh load', async () => {
    vi.resetModules();
    const fresh = await import('./store');
    expect(fresh.useSessionStore.getState().status).toBe('restoring');
    expect(fresh.useSessionStore.getState().accessToken).toBeNull();
  });

  it('signIn stores the token and marks the session signed in', () => {
    useSessionStore.getState().signIn('tok-1'); // secrets-check: allow
    expect(useSessionStore.getState().accessToken).toBe('tok-1');
    expect(useSessionStore.getState().status).toBe('signed_in');
  });

  it('signIn sets the token before the status, so a signed_in subscriber already has it', () => {
    const seen: (string | null)[] = [];
    const unsubscribe = useSessionStore.subscribe((state) => {
      if (state.status === 'signed_in') seen.push(state.accessToken);
    });
    useSessionStore.getState().signIn('tok-1'); // secrets-check: allow
    unsubscribe();
    expect(seen.length).toBeGreaterThan(0);
    expect(seen.every((token) => token === 'tok-1')).toBe(true);
  });

  it('setAccessToken replaces the token without changing the status', () => {
    useSessionStore.getState().signIn('one'); // secrets-check: allow
    useSessionStore.getState().setAccessToken('two'); // secrets-check: allow
    expect(useSessionStore.getState().accessToken).toBe('two');
    expect(useSessionStore.getState().status).toBe('signed_in');
  });

  it('signOut drops the token and marks the session signed out', () => {
    useSessionStore.getState().signIn('tok-1'); // secrets-check: allow
    useSessionStore.getState().signOut();
    expect(useSessionStore.getState().accessToken).toBeNull();
    expect(useSessionStore.getState().status).toBe('signed_out');
  });

  it('never touches Web Storage', () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem');
    useSessionStore.getState().signIn('tok-1'); // secrets-check: allow
    useSessionStore.getState().signOut();
    expect(setItem).not.toHaveBeenCalled();
    expect(localStorage).toHaveLength(0);
    expect(sessionStorage).toHaveLength(0);
  });
});

describe('memoryTokenStore is an adapter over the session store', () => {
  it('getAccessToken reads the store', async () => {
    useSessionStore.getState().setAccessToken('from-store'); // secrets-check: allow
    expect(await memoryTokenStore.getAccessToken()).toBe('from-store');
  });

  it('setTokens writes the token into the store', async () => {
    await memoryTokenStore.setTokens({ accessToken: 'written' }); // secrets-check: allow
    expect(useSessionStore.getState().accessToken).toBe('written');
  });

  it('clear nulls the token in the store', async () => {
    useSessionStore.getState().setAccessToken('x'); // secrets-check: allow
    await memoryTokenStore.clear();
    expect(useSessionStore.getState().accessToken).toBeNull();
  });

  it('has no getRefreshToken (cookie mode)', () => {
    expect(memoryTokenStore.getRefreshToken).toBeUndefined();
  });
});

describe('the app api client uses the session store', () => {
  it('sends the token the store holds after signIn as the Bearer header', async () => {
    useSessionStore.getState().signIn('abc'); // secrets-check: allow
    const seen: (string | null)[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        seen.push(new Request(input, init).headers.get('Authorization'));
        return json({ status: 'ok' });
      }),
    );
    await api.GET('/api/v1/health/live');
    expect(seen).toEqual(['Bearer abc']);
  });

  it('onSessionExpired signs the store out when the refresh is rejected', async () => {
    useSessionStore.getState().signIn('expired'); // secrets-check: allow
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = new Request(input, init).url;
        return unauthorized(url.endsWith(REFRESH_PATH) ? 'invalid_refresh' : 'token_expired');
      }),
    );
    await expect(api.GET('/api/v1/health/live')).rejects.toMatchObject({ status: 401 });
    expect(useSessionStore.getState().status).toBe('signed_out');
    expect(useSessionStore.getState().accessToken).toBeNull();
  });

  it('keeps the refreshed token in the store and stays signed in', async () => {
    useSessionStore.getState().signIn('expired'); // secrets-check: allow
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const request = new Request(input, init);
        if (request.url.endsWith(REFRESH_PATH)) {
          return json({ access_token: 'fresh', token_type: 'bearer', expires_in: 900 }); // secrets-check: allow
        }
        return request.headers.get('Authorization') === 'Bearer fresh'
          ? json({ status: 'ok' })
          : unauthorized('token_expired');
      }),
    );
    await api.GET('/api/v1/health/live');
    expect(useSessionStore.getState().accessToken).toBe('fresh');
    expect(useSessionStore.getState().status).toBe('signed_in');
  });
});
