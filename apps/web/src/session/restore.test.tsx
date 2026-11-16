// S-2.1.6 AC5 (T-2.1.6.2): session restore on page load. Boots the real entry point (main.tsx:
// providers, route table, router on the browser history) with a stubbed fetch, so it holds whether
// the restore is started from main.tsx or from a top-level component.
import { screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

interface Call {
  path: string;
  method: string;
  credentials: RequestCredentials;
  headers: Headers;
  body: string;
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
const failure = (status: number, code: string) =>
  json({ error: { code, message: `${code} message`, details: null, request_id: 'r' } }, status);

const ACCESS = 'restored-access-token'; // secrets-check: allow
const refreshed = () => json({ access_token: ACCESS, token_type: 'bearer', expires_in: 900 }); // secrets-check: allow

/** A refresh that stays pending until `settle` is called. */
function deferredRefresh() {
  let settle: (response: Response) => void = () => undefined;
  const pending = new Promise<Response>((resolve) => {
    settle = resolve;
  });
  return { pending, settle };
}

function stubFetch(refresh: (call: Call) => Response | Promise<Response>) {
  const calls: Call[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = new Request(input, init);
      const call: Call = {
        path: new URL(request.url).pathname,
        method: request.method,
        credentials: request.credentials,
        headers: request.headers,
        body: await request.text(),
      };
      calls.push(call);
      if (call.path === '/api/v1/auth/refresh') return refresh(call);
      return json({ status: 'ok' });
    }),
  );
  return { calls, refreshCalls: () => calls.filter((c) => c.path === '/api/v1/auth/refresh') };
}

/** Loads the app the way a page load does, with a fresh module graph and the URL already set. */
async function boot(url: string) {
  document.body.innerHTML = '<div id="root"></div>';
  window.history.pushState({}, '', url);
  vi.resetModules();
  await import('../main');
  const { useSessionStore } = await import('./store');
  return { useSessionStore };
}

const heading = (name: string) => screen.queryByRole('heading', { level: 2, name });

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

afterEach(() => {
  document.body.innerHTML = '';
  window.history.pushState({}, '', '/');
  vi.unstubAllGlobals();
});

describe('AC5: session restore before protected content renders', () => {
  it('is restoring on load and renders no protected content while the refresh is pending', async () => {
    const refresh = deferredRefresh();
    stubFetch(() => refresh.pending);
    const { useSessionStore } = await boot('/decks');
    expect(useSessionStore.getState().status).toBe('restoring');
    expect(await screen.findByRole('status', { name: 'Loading page' })).toBeInTheDocument();
    expect(heading('Decks')).not.toBeInTheDocument();
    expect(heading('Sign in')).not.toBeInTheDocument();
    expect(window.location.pathname).toBe('/decks');
    refresh.settle(refreshed());
    await waitFor(() => expect(heading('Decks')).toBeInTheDocument());
  });

  it('a successful refresh signs in and renders the page at the same URL', async () => {
    const api = stubFetch(() => refreshed());
    const { useSessionStore } = await boot('/decks?x=1');
    expect(await screen.findByRole('heading', { level: 2, name: 'Decks' })).toBeInTheDocument();
    expect(window.location.pathname).toBe('/decks');
    expect(window.location.search).toBe('?x=1');
    expect(useSessionStore.getState().status).toBe('signed_in');
    expect(useSessionStore.getState().accessToken).toBe(ACCESS);
    expect(api.refreshCalls()).toHaveLength(1);
  });

  it('sends the refresh as a POST with credentials include and no body', async () => {
    const api = stubFetch(() => refreshed());
    await boot('/decks');
    await screen.findByRole('heading', { level: 2, name: 'Decks' });
    const call = api.refreshCalls()[0]!;
    expect(call.method).toBe('POST');
    expect(call.credentials).toBe('include');
    expect(call.body).toBe('');
  });

  it('never visits /sign-in while the session restores as signed in', async () => {
    const refresh = deferredRefresh();
    stubFetch(() => refresh.pending);
    await boot('/games');
    const visited: string[] = [window.location.pathname];
    const onPop = () => visited.push(window.location.pathname);
    window.addEventListener('popstate', onPop);
    refresh.settle(refreshed());
    await screen.findByRole('heading', { level: 2, name: 'Games' });
    window.removeEventListener('popstate', onPop);
    expect(window.location.pathname).toBe('/games');
    expect(visited.every((p) => p === '/games')).toBe(true);
  });

  it('a rejected refresh redirects to sign-in with the page as next', async () => {
    stubFetch(() => failure(401, 'invalid_refresh'));
    const { useSessionStore } = await boot('/decks');
    expect(await screen.findByRole('heading', { level: 2, name: 'Sign in' })).toBeInTheDocument();
    expect(window.location.pathname).toBe('/sign-in');
    expect(new URLSearchParams(window.location.search).get('next')).toBe('/decks');
    expect(useSessionStore.getState().status).toBe('signed_out');
    expect(useSessionStore.getState().accessToken).toBeNull();
  });

  it('a failed refresh (server error) ends signed out and goes to sign-in', async () => {
    stubFetch(() => failure(503, 'service_unavailable'));
    const { useSessionStore } = await boot('/stats');
    expect(await screen.findByRole('heading', { level: 2, name: 'Sign in' })).toBeInTheDocument();
    expect(useSessionStore.getState().status).toBe('signed_out');
    expect(new URLSearchParams(window.location.search).get('next')).toBe('/stats');
  });

  it('a network failure on refresh ends signed out and goes to sign-in', async () => {
    stubFetch(() => {
      throw new TypeError('Failed to fetch');
    });
    const { useSessionStore } = await boot('/collection');
    expect(await screen.findByRole('heading', { level: 2, name: 'Sign in' })).toBeInTheDocument();
    expect(useSessionStore.getState().status).toBe('signed_out');
  });

  it('refreshes once even under StrictMode', async () => {
    const api = stubFetch(() => refreshed());
    await boot('/leagues');
    await screen.findByRole('heading', { level: 2, name: 'Leagues' });
    expect(api.refreshCalls()).toHaveLength(1);
  });

  it('lets a public page render without waiting for the restore', async () => {
    const refresh = deferredRefresh();
    stubFetch(() => refresh.pending);
    await boot('/about');
    expect(await screen.findByRole('heading', { level: 2, name: 'About' })).toBeInTheDocument();
    expect(screen.queryByRole('status', { name: 'Loading page' })).not.toBeInTheDocument();
    refresh.settle(failure(401, 'invalid_refresh'));
  });
});

describe('AC5: the access token exists only in memory', () => {
  it('leaves localStorage and sessionStorage empty after a restore', async () => {
    stubFetch(() => refreshed());
    const setItem = vi.spyOn(Storage.prototype, 'setItem');
    await boot('/decks');
    await screen.findByRole('heading', { level: 2, name: 'Decks' });
    expect(setItem).not.toHaveBeenCalled();
    expect(localStorage).toHaveLength(0);
    expect(sessionStorage).toHaveLength(0);
    setItem.mockRestore();
  });

  it('puts the restored token on the next API request as a Bearer header', async () => {
    const api = stubFetch(() => refreshed());
    await boot('/decks');
    await screen.findByRole('heading', { level: 2, name: 'Decks' });
    const { api: client } = await import('../api');
    await client.GET('/api/v1/health/live');
    const live = api.calls.filter((c) => c.path === '/api/v1/health/live');
    expect(live[0]!.headers.get('Authorization')).toBe(`Bearer ${ACCESS}`);
  });

  it('does not put the token in the DOM or in document.cookie', async () => {
    stubFetch(() => refreshed());
    await boot('/decks');
    await screen.findByRole('heading', { level: 2, name: 'Decks' });
    expect(document.body.innerHTML).not.toContain(ACCESS);
    expect(document.cookie).not.toContain(ACCESS);
  });
});
