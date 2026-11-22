// S-2.1.12, second review round: sign-in clears the pending-logout flag, a logout queued behind a
// held Web Lock times out instead of hanging, restore honours a flag set during its refresh,
// "Sign out everywhere" also calls POST /auth/logout, and the plain Sign out button's pending state.
import { StrictMode } from 'react';
import { act, configure, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { REFRESH_LOCK } from '@mtg-stats/api-client';
import { api } from '../api';
import { AppRoot } from '../providers/AppRoot';
import { routes } from '../routes';
import { FakeBroadcastChannel } from '../test/broadcast-channel';
import { useToastStore } from '../ui/toasts';
import { LOGOUT_PENDING_KEY, setLogoutPending } from './pending-logout';
import { restoreSession } from './restore';
import { startSignOutSync } from './sign-out';
import { useSessionStore } from './store';
import { LOGOUT_PENDING_WARNING } from './useSignOut';

vi.stubGlobal('BroadcastChannel', FakeBroadcastChannel);
configure({ asyncUtilTimeout: 5000 });

const ACCESS = 'access-token-1'; // secrets-check: allow
const LOGIN_ACCESS = 'access-after-login'; // secrets-check: allow
const LATE = 'late-access-token'; // secrets-check: allow
const LOGOUT = '/api/v1/auth/logout';
const LOGOUT_ALL = '/api/v1/auth/logout-all';
const REFRESH = '/api/v1/auth/refresh';
const LOGIN = '/api/v1/auth/login';

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const tokenResponse = (access = LOGIN_ACCESS) =>
  json({ access_token: access, token_type: 'bearer', expires_in: 900 }); // secrets-check: allow
const failure = (status: number, code: string) =>
  json({ error: { code, message: 'x', details: null, request_id: 'r' } }, status);

type Mode = 'ok' | '500' | 'network' | 'hang';

/** Stubs fetch and records every call. Refresh answers 401 unless `refresh` is given. */
function stubApi(
  opts: {
    logout?: Mode;
    logoutAll?: Mode;
    refresh?: (request: Request) => Response | Promise<Response>;
  } = {},
) {
  const calls: string[] = [];
  const answer = (request: Request, mode: Mode = 'ok') => {
    if (mode === 'network') return Promise.reject(new TypeError('Failed to fetch'));
    if (mode === 'hang') {
      return new Promise<Response>((_resolve, reject) => {
        request.signal.addEventListener('abort', () => {
          reject(new DOMException('The operation was aborted.', 'AbortError'));
        });
      });
    }
    if (mode === '500') return Promise.resolve(failure(500, 'internal'));
    return Promise.resolve(new Response(null, { status: 204 }));
  };
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const request = new Request(input, init);
      const path = new URL(request.url).pathname;
      calls.push(path);
      // The shell's playgroup switcher (S-2.4.5) loads the list: an empty page, never a 401.
      if (path === '/api/v1/playgroups')
        return Promise.resolve(json({ items: [], next_cursor: null }));
      if (path === LOGOUT) return answer(request, opts.logout);
      if (path === LOGOUT_ALL) return answer(request, opts.logoutAll);
      if (path === REFRESH && opts.refresh) return Promise.resolve(opts.refresh(request));
      if (path === LOGIN) return Promise.resolve(tokenResponse());
      if (path === '/api/v1/auth/change-password') return Promise.resolve(tokenResponse());
      if (path === '/api/v1/auth/register') return Promise.resolve(tokenResponse());
      if (path === '/api/v1/invites/preview') {
        return Promise.resolve(
          json({ inviter_display_name: 'Ada Lovelace', expires_at: '2030-01-01T00:00:00Z' }),
        );
      }
      return Promise.resolve(failure(401, 'invalid_refresh'));
    }),
  );
  return { calls, count: (path: string) => calls.filter((c) => c === path).length };
}

function mount(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(
    <StrictMode>
      <AppRoot router={router} />
    </StrictMode>,
  );
  return router;
}

type User = ReturnType<typeof userEvent.setup>;

/** Fake setTimeout/clearTimeout (the 4 s logout timeout) while real time still flows for waitFor. */
function useFakeClock(): User {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'], shouldAdvanceTime: true });
  return userEvent.setup({
    advanceTimers: (ms) => {
      vi.advanceTimersByTime(ms);
    },
  });
}
const pastTimeout = () =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(4000);
  });

const flag = () => localStorage.getItem(LOGOUT_PENDING_KEY);
const resetStore = (status: 'signed_in' | 'signed_out' | 'restoring') => {
  useSessionStore.setState({
    status,
    accessToken: status === 'signed_in' ? ACCESS : null,
    signOutReason: null,
  });
};

/**
 * An EXCLUSIVE fake navigator.locks: callers queue by name, `{ signal }` is honoured (a caller
 * still queued when the signal aborts rejects with an AbortError and leaves the queue).
 */
function installExclusiveLocks() {
  const held = new Set<string>();
  const queues = new Map<string, (() => void)[]>();
  const next = (name: string) => {
    held.delete(name);
    queues.get(name)?.shift()?.();
  };
  const request = (name: string, a: unknown, b?: unknown) => {
    const options = (typeof a === 'function' ? {} : a) as { signal?: AbortSignal };
    const callback = (typeof a === 'function' ? a : b) as () => Promise<unknown>;
    return new Promise<unknown>((resolve, reject) => {
      const signal = options.signal;
      const abortError = () => new DOMException('The request was aborted.', 'AbortError');
      if (signal?.aborted) {
        reject(abortError());
        return;
      }
      const run = () => {
        held.add(name);
        void callback().then(
          (value) => {
            resolve(value);
            next(name);
          },
          (error: unknown) => {
            reject(error as Error);
            next(name);
          },
        );
      };
      if (!held.has(name)) {
        run();
        return;
      }
      const entry = () => {
        signal?.removeEventListener('abort', onAbort);
        run();
      };
      const onAbort = () => {
        const q = queues.get(name) ?? [];
        const at = q.indexOf(entry);
        if (at >= 0) q.splice(at, 1);
        reject(abortError());
      };
      queues.set(name, [...(queues.get(name) ?? []), entry]);
      signal?.addEventListener('abort', onAbort, { once: true });
    });
  };
  Object.defineProperty(navigator, 'locks', { configurable: true, value: { request } });
  return { request };
}

let stopSync: () => void = () => undefined;

beforeEach(() => {
  localStorage.clear();
  FakeBroadcastChannel.posts = [];
  resetStore('signed_in');
  stopSync = startSignOutSync();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.stubGlobal('BroadcastChannel', FakeBroadcastChannel);
  Reflect.deleteProperty(navigator, 'locks');
  localStorage.clear();
  useToastStore.setState({ toasts: [] });
  stopSync();
  resetStore('signed_out');
});

const signInViaForm = async (user: User, name: string) => {
  await user.type(screen.getByLabelText(/username/i), name);
  await user.type(screen.getByLabelText(/password/i), 'correct horse battery');
  await user.click(screen.getByRole('button', { name: /sign in/i }));
};

const loadAgainAndRestore = async (opts: Parameters<typeof stubApi>[0]) => {
  resetStore('restoring');
  const seen = stubApi(opts);
  await restoreSession(api);
  return seen;
};

describe('Sign-in clears the pending-logout flag', () => {
  it('clears the flag in the store signIn', () => {
    setLogoutPending();
    expect(flag()).not.toBeNull();
    resetStore('signed_out');
    useSessionStore.getState().signIn(LOGIN_ACCESS);
    expect(flag()).toBeNull();
    expect(useSessionStore.getState().status).toBe('signed_in');
  });

  it('does not throw when storage is blocked while signing in', () => {
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    resetStore('signed_out');
    expect(() => {
      useSessionStore.getState().signIn(LOGIN_ACCESS);
    }).not.toThrow();
    expect(useSessionStore.getState().status).toBe('signed_in');
  });

  it('SignInPage: signing in clears the flag, the next load refreshes, stays signed in and never calls /auth/logout', async () => {
    setLogoutPending();
    resetStore('signed_out');
    const seen = stubApi();
    const user = userEvent.setup();
    const router = mount('/sign-in');
    await signInViaForm(user, 'ada');
    await waitFor(() => expect(router.state.location.pathname).toBe('/'));
    expect(flag()).toBeNull();
    const second = await loadAgainAndRestore({
      refresh: () => tokenResponse(LATE),
    });
    expect(second.count(REFRESH)).toBe(1);
    expect(second.count(LOGOUT)).toBe(0);
    expect(seen.count(LOGOUT)).toBe(0);
    expect(useSessionStore.getState().status).toBe('signed_in');
  });

  it('a different user signing in on that browser is not forced out on the next load', async () => {
    // User A signs out while offline: the flag is set. User B then signs in on the same browser.
    setLogoutPending();
    resetStore('signed_out');
    stubApi();
    const user = userEvent.setup();
    mount('/sign-in');
    await signInViaForm(user, 'grace');
    await waitFor(() => expect(useSessionStore.getState().status).toBe('signed_in'));
    const second = await loadAgainAndRestore({ refresh: () => tokenResponse(LATE) });
    expect(second.count(LOGOUT)).toBe(0);
    expect(useSessionStore.getState().status).toBe('signed_in');
    expect(useSessionStore.getState().accessToken).toBe(LATE);
  });

  it('InvitePage: registering clears the flag', async () => {
    setLogoutPending();
    resetStore('signed_out');
    stubApi();
    const user = userEvent.setup();
    mount('/invite/inv-token-123');
    await screen.findByText(/Ada Lovelace/);
    await user.type(screen.getByLabelText(/username/i), 'ada');
    await user.type(screen.getByLabelText(/display name/i), 'Ada');
    await user.type(screen.getByLabelText(/password/i), 'correct horse battery');
    await user.click(screen.getByRole('button', { name: /register|create account|sign up|join/i }));
    await waitFor(() => expect(useSessionStore.getState().status).toBe('signed_in'));
    expect(flag()).toBeNull();
  });

  it('AccountPage: changing the password (a new session cookie) clears the flag', async () => {
    setLogoutPending();
    stubApi();
    const user = userEvent.setup();
    mount('/account');
    await screen.findByRole('heading', { level: 2, name: 'Account' });
    for (const [label, value] of [
      ['Current password', 'current password 1'],
      ['New password', 'a brand new password'],
      ['Confirm new password', 'a brand new password'],
    ] as const) {
      await user.click(screen.getByLabelText(label));
      await user.paste(value);
    }
    await user.click(screen.getByRole('button', { name: /change password/i }));
    await waitFor(() => expect(useSessionStore.getState().accessToken).toBe(LOGIN_ACCESS));
    expect(flag()).toBeNull();
  });

  it('a flag that never clears (/logout always failing) never blocks sign-in', async () => {
    stubApi({ logout: '500' });
    const user = userEvent.setup();
    const router = mount('/decks');
    await user.click(screen.getByRole('button', { name: 'Account menu' }));
    await user.click(screen.getByRole('button', { name: 'Sign out' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/sign-in'));
    expect(flag()).not.toBeNull();
    await signInViaForm(user, 'ada');
    await waitFor(() => expect(useSessionStore.getState().status).toBe('signed_in'));
    await waitFor(() => expect(router.state.location.pathname).not.toBe('/sign-in'));
    // The next load: the flag was cleared by the sign-in, so refresh runs and logout is not called.
    const second = await loadAgainAndRestore({ logout: '500', refresh: () => tokenResponse(LATE) });
    expect(second.count(LOGOUT)).toBe(0);
    expect(useSessionStore.getState().status).toBe('signed_in');
  });
});

describe('A logout queued behind a held Web Lock', () => {
  /** A refresh that holds REFRESH_LOCK until `release()` (a real client refresh over a hung fetch). */
  function holdRefreshLock() {
    installExclusiveLocks();
    let release: () => void = () => undefined;
    const gate = new Promise<Response>((resolve) => {
      release = () => {
        resolve(failure(401, 'invalid_refresh'));
      };
    });
    const seen = stubApi({ refresh: () => gate });
    const hung = api.refreshSession().catch(() => false);
    return {
      seen,
      ready: () => waitFor(() => expect(seen.count(REFRESH)).toBe(1)),
      finish: async () => {
        release();
        await hung;
      },
    };
  }

  it('the fake lock is exclusive and honours abort (self-check)', async () => {
    const { request } = installExclusiveLocks();
    const order: string[] = [];
    void request(REFRESH_LOCK, () => {
      order.push('holder');
      return new Promise(() => undefined);
    });
    const controller = new AbortController();
    const queued = request(REFRESH_LOCK, { signal: controller.signal }, () => {
      order.push('queued');
      return Promise.resolve();
    });
    controller.abort();
    await expect(queued).rejects.toMatchObject({ name: 'AbortError' });
    expect(order).toEqual(['holder']);
  });

  it('plain sign out times out after 4 s, signs out locally, sets the flag and warns', async () => {
    const lock = holdRefreshLock();
    const user = useFakeClock();
    const router = mount('/decks');
    await lock.ready();
    await user.click(screen.getByRole('button', { name: 'Account menu' }));
    await user.click(screen.getByRole('button', { name: 'Sign out' }));
    expect(useSessionStore.getState().status).toBe('signed_in'); // still queued behind the refresh
    await pastTimeout();
    await waitFor(() => expect(router.state.location.pathname).toBe('/sign-in'));
    expect(useSessionStore.getState().status).toBe('signed_out');
    expect(useSessionStore.getState().accessToken).toBeNull();
    expect(flag()).not.toBeNull();
    expect(lock.seen.count(LOGOUT)).toBe(0); // the call never left the queue
    expect(await screen.findByText(LOGOUT_PENDING_WARNING)).toBeInTheDocument();
    await lock.finish();
  });

  it('startup restore with the flag set does not stay on Loading… behind a hung refresh', async () => {
    const lock = holdRefreshLock();
    setLogoutPending();
    resetStore('restoring');
    const user = useFakeClock();
    void user;
    const router = mount('/decks');
    await lock.ready();
    const done = restoreSession(api);
    expect(useSessionStore.getState().status).toBe('restoring');
    await pastTimeout();
    await done;
    expect(useSessionStore.getState().status).toBe('signed_out');
    expect(flag()).not.toBeNull(); // not confirmed by the server: the next load retries
    expect(lock.seen.count(LOGOUT)).toBe(0);
    await waitFor(() => expect(router.state.location.pathname).toBe('/sign-in'));
    await lock.finish();
  });
});

describe('Restore honours a flag set during the refresh', () => {
  beforeEach(() => {
    resetStore('restoring');
  });

  it('treats the session as signed out, calls /auth/logout and clears the flag on success', async () => {
    const seen = stubApi({
      refresh: () => {
        setLogoutPending(); // another tab signed out while this refresh was in flight
        return tokenResponse(LATE);
      },
    });
    await restoreSession(api);
    expect(seen.count(REFRESH)).toBe(1);
    expect(seen.count(LOGOUT)).toBe(1);
    expect(useSessionStore.getState().status).toBe('signed_out');
    expect(useSessionStore.getState().accessToken).toBeNull();
    expect(flag()).toBeNull();
  });

  it('keeps the flag when that logout fails, and still ends signed out', async () => {
    const seen = stubApi({
      logout: '500',
      refresh: () => {
        setLogoutPending();
        return tokenResponse(LATE);
      },
    });
    await restoreSession(api);
    expect(seen.count(LOGOUT)).toBe(1);
    expect(useSessionStore.getState().status).toBe('signed_out');
    expect(useSessionStore.getState().accessToken).toBeNull();
    expect(flag()).not.toBeNull();
  });
});

describe('Sign out everywhere also calls POST /auth/logout', () => {
  const confirmEverywhere = async (user: User) => {
    await user.click(screen.getByRole('button', { name: 'Account menu' }));
    await user.click(screen.getByRole('button', { name: 'Sign out everywhere' }));
    const dialog = screen.getByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Sign out everywhere' }));
  };

  it('calls logout-all, then logout, then signs out locally', async () => {
    const seen = stubApi();
    const user = userEvent.setup();
    const router = mount('/decks');
    await confirmEverywhere(user);
    await waitFor(() => expect(router.state.location.pathname).toBe('/sign-in'));
    expect(seen.calls.filter((c) => c === LOGOUT_ALL || c === LOGOUT)).toEqual([
      LOGOUT_ALL,
      LOGOUT,
    ]);
    expect(useSessionStore.getState().status).toBe('signed_out');
  });

  it('still signs out locally when the extra /auth/logout call fails', async () => {
    const seen = stubApi({ logout: '500' });
    const user = userEvent.setup();
    const router = mount('/decks');
    await confirmEverywhere(user);
    await waitFor(() => expect(router.state.location.pathname).toBe('/sign-in'));
    expect(seen.count(LOGOUT_ALL)).toBe(1);
    expect(seen.count(LOGOUT)).toBe(1);
    expect(useSessionStore.getState().status).toBe('signed_out');
    expect(useSessionStore.getState().accessToken).toBeNull();
  });

  it('does not call /auth/logout when logout-all fails (the user stays signed in)', async () => {
    const seen = stubApi({ logoutAll: '500' });
    const user = userEvent.setup();
    mount('/decks');
    await confirmEverywhere(user);
    expect(await within(screen.getByRole('dialog')).findByRole('alert')).toBeInTheDocument();
    expect(seen.count(LOGOUT)).toBe(0);
    expect(useSessionStore.getState().status).toBe('signed_in');
  });
});

describe('Plain Sign out button while pending', () => {
  it('uses aria-disabled (not disabled), keeps focus, and a second click sends no second request', async () => {
    const seen = stubApi({ logout: 'hang' });
    const user = useFakeClock();
    const router = mount('/decks');
    await user.click(screen.getByRole('button', { name: 'Account menu' }));
    await user.click(screen.getByRole('button', { name: 'Sign out' }));
    const pending = await screen.findByRole('button', { name: /signing out/i });
    expect(pending).toHaveAttribute('aria-disabled', 'true');
    expect(pending).not.toBeDisabled();
    expect(pending).toHaveFocus();
    await user.click(pending);
    expect(seen.count(LOGOUT)).toBe(1);
    await pastTimeout();
    await waitFor(() => expect(router.state.location.pathname).toBe('/sign-in'));
    expect(seen.count(LOGOUT)).toBe(1);
  });
});
