// S-2.1.12: sign out, sign out everywhere, cross-tab sync, session expiry, keyboard, registry.
import { StrictMode } from 'react';
import { QueryClient } from '@tanstack/react-query';
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
import { LOGOUT_PENDING_KEY } from './pending-logout';
import { restoreSession } from './restore';
import {
  clearLocalSession,
  handleSessionExpired,
  SIGN_OUT_CHANNEL,
  startSignOutSync,
} from './sign-out';
import { useSessionStore } from './store';
import { memoryTokenStore } from './tokens';
import { registerUserStateReset, resetUserState } from './user-state';
import { LOGOUT_PENDING_WARNING, SIGN_OUT_EVERYWHERE_FAILED } from './useSignOut';

vi.stubGlobal('BroadcastChannel', FakeBroadcastChannel);

// Every flow here mounts the whole app (router, providers, StrictMode) and the first test in a
// worker also pays the cold start of that module graph, which under a loaded full suite can exceed
// Testing Library's 1000 ms default. Assertions wait with `waitFor`/`find*` (never a synchronous
// read right after a navigation), and this raises only the ceiling, not the polling.
configure({ asyncUtilTimeout: 5000 });

const ACCESS = 'access-token-1'; // secrets-check: allow
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

type Mode = 'ok' | '500' | 'network' | 'hang';

/**
 * Stubs fetch and records every call path+method. Refresh always 401s unless `refresh` is given.
 * 'hang' never answers but, like a real fetch, rejects with an AbortError when the request's
 * signal aborts (recorded in `aborted`).
 */
function stubApi(
  opts: {
    logout?: Mode;
    logoutAll?: Mode;
    refresh?: (request: Request) => Response | Promise<Response>;
    onCall?: (path: string) => void;
  } = {},
) {
  const calls: { path: string; method: string }[] = [];
  const aborted: string[] = [];
  const answer = (path: string, request: Request, mode: Mode = 'ok') => {
    if (mode === 'network') return Promise.reject(new TypeError('Failed to fetch'));
    if (mode === 'hang') {
      return new Promise<Response>((_resolve, reject) => {
        request.signal.addEventListener('abort', () => {
          aborted.push(path);
          reject(new DOMException('The operation was aborted.', 'AbortError'));
        });
      });
    }
    if (mode === '500') {
      return Promise.resolve(
        json({ error: { code: 'internal', message: 'boom', details: null, request_id: 'r' } }, 500),
      );
    }
    return Promise.resolve(new Response(null, { status: 204 }));
  };
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const request = new Request(input, init);
      const path = new URL(request.url).pathname;
      calls.push({ path, method: request.method });
      opts.onCall?.(path);
      if (path === '/api/v1/auth/logout') return answer(path, request, opts.logout);
      if (path === '/api/v1/auth/logout-all') return answer(path, request, opts.logoutAll);
      if (path === '/api/v1/auth/refresh' && opts.refresh) {
        return Promise.resolve(opts.refresh(request));
      }
      if (path === '/api/v1/auth/login') {
        return Promise.resolve(
          json({ access_token: 'new-access', token_type: 'bearer', expires_in: 900 }), // secrets-check: allow
        );
      }
      return Promise.resolve(
        json(
          { error: { code: 'invalid_refresh', message: 'x', details: null, request_id: 'r' } },
          401,
        ),
      );
    }),
  );
  return {
    calls,
    aborted,
    count: (path: string) => calls.filter((c) => c.path === path).length,
  };
}

function mount(path: string, queryClient?: QueryClient) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(
    <StrictMode>
      <AppRoot router={router} {...(queryClient ? { queryClient } : {})} />
    </StrictMode>,
  );
  return router;
}

let stopSync: () => void = () => undefined;

beforeEach(() => {
  localStorage.clear();
  FakeBroadcastChannel.posts = [];
  useSessionStore.setState({ status: 'signed_in', accessToken: ACCESS, signOutReason: null });
  stopSync = startSignOutSync();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  localStorage.clear();
  useToastStore.setState({ toasts: [] });
  stopSync();
  useSessionStore.setState({ status: 'signed_out', accessToken: null, signOutReason: null });
});

type User = ReturnType<typeof userEvent.setup>;

const openMenu = async (user: User) => {
  await user.click(screen.getByRole('button', { name: 'Account menu' }));
};

describe('Sign out', () => {
  it('is not offered to a signed-out visitor', () => {
    useSessionStore.setState({ status: 'signed_out', accessToken: null });
    mount('/about');
    expect(screen.queryByRole('button', { name: 'Account menu' })).not.toBeInTheDocument();
  });

  it('calls POST /api/v1/auth/logout, clears token, cache and stores, and shows /sign-in', async () => {
    const api = stubApi();
    const qc = new QueryClient();
    qc.setQueryData(['decks'], [{ id: 1 }]);
    const reset = vi.fn();
    const unregister = registerUserStateReset(reset);
    const user = userEvent.setup();
    const router = mount('/decks', qc);
    await openMenu(user);
    await user.click(screen.getByRole('button', { name: 'Sign out' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/sign-in'));
    expect(api.calls.filter((c) => c.path === '/api/v1/auth/logout')).toEqual([
      { path: '/api/v1/auth/logout', method: 'POST' },
    ]);
    expect(useSessionStore.getState().accessToken).toBeNull();
    expect(useSessionStore.getState().status).toBe('signed_out');
    expect(qc.getQueryData(['decks'])).toBeUndefined();
    expect(reset).toHaveBeenCalled();
    // The router state changes before React commits the new route: wait for the heading, never
    // read it synchronously (that race was the intermittent failure under load).
    expect(await screen.findByRole('heading', { level: 2, name: 'Sign in' })).toBeInTheDocument();
    unregister();
  });

  it('leaves a plain /sign-in with no next and no expired message', async () => {
    stubApi();
    const user = userEvent.setup();
    const router = mount('/decks?a=1#h');
    await openMenu(user);
    await user.click(screen.getByRole('button', { name: 'Sign out' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/sign-in'));
    expect(router.state.location.search).toBe('');
    expect(screen.queryByText('Your session has expired')).not.toBeInTheDocument();
  });

  it.each<Mode>(['network', '500'])(
    'still signs out locally when the logout call fails (%s)',
    async (mode) => {
      stubApi({ logout: mode });
      const user = userEvent.setup();
      const router = mount('/decks');
      await openMenu(user);
      await user.click(screen.getByRole('button', { name: 'Sign out' }));
      await waitFor(() => expect(router.state.location.pathname).toBe('/sign-in'));
      expect(useSessionStore.getState().accessToken).toBeNull();
      expect(useSessionStore.getState().status).toBe('signed_out');
    },
  );
});

describe('Sign out in other tabs', () => {
  it('signs this tab out within 1 s when another tab signs out', async () => {
    stubApi();
    const router = mount('/decks');
    const otherTab = new FakeBroadcastChannel(SIGN_OUT_CHANNEL);
    const started = Date.now();
    otherTab.postMessage({ type: 'signed_out' });
    await waitFor(() => expect(router.state.location.pathname).toBe('/sign-in'), {
      timeout: 1000,
    });
    expect(Date.now() - started).toBeLessThan(1000);
    expect(useSessionStore.getState().accessToken).toBeNull();
    expect(router.state.location.search).toBe('');
    otherTab.close();
  });

  it('broadcasts exactly once on sign out', async () => {
    stubApi();
    const otherTab = new FakeBroadcastChannel(SIGN_OUT_CHANNEL);
    const heard = vi.fn();
    otherTab.addEventListener('message', heard);
    const user = userEvent.setup();
    mount('/decks');
    await openMenu(user);
    await user.click(screen.getByRole('button', { name: 'Sign out' }));
    await waitFor(() => expect(heard).toHaveBeenCalledTimes(1));
    await new Promise((r) => setTimeout(r, 50));
    expect(heard).toHaveBeenCalledTimes(1);
    expect(FakeBroadcastChannel.posts).toHaveLength(1);
    otherTab.close();
  });

  it('does not rebroadcast when it signs out because another tab did', async () => {
    stubApi();
    mount('/decks');
    const otherTab = new FakeBroadcastChannel(SIGN_OUT_CHANNEL);
    otherTab.postMessage({ type: 'signed_out' });
    await waitFor(() => expect(useSessionStore.getState().status).toBe('signed_out'));
    await new Promise((r) => setTimeout(r, 50));
    expect(FakeBroadcastChannel.posts).toHaveLength(1); // only the other tab's own post
    otherTab.close();
  });

  it('ignores unrelated messages', async () => {
    const otherTab = new FakeBroadcastChannel(SIGN_OUT_CHANNEL);
    otherTab.postMessage({ type: 'something_else' });
    otherTab.postMessage(null);
    await new Promise((r) => setTimeout(r, 50));
    expect(useSessionStore.getState().status).toBe('signed_in');
    otherTab.close();
  });

  it('breaks nothing when BroadcastChannel is undefined', async () => {
    vi.resetModules();
    vi.stubGlobal('BroadcastChannel', undefined);
    try {
      const mod = await import('./sign-out');
      const store = await import('./store');
      store.useSessionStore.setState({ status: 'signed_in', accessToken: ACCESS });
      const stop = mod.startSignOutSync();
      expect(() => {
        mod.completeSignOut();
      }).not.toThrow();
      expect(store.useSessionStore.getState().status).toBe('signed_out');
      expect(store.useSessionStore.getState().accessToken).toBeNull();
      expect(() => {
        stop();
      }).not.toThrow();
    } finally {
      vi.stubGlobal('BroadcastChannel', FakeBroadcastChannel);
      vi.resetModules();
    }
  });
});

describe('Sign out everywhere', () => {
  it('asks for confirmation, then calls POST /api/v1/auth/logout-all and signs out locally', async () => {
    const api = stubApi();
    const user = userEvent.setup();
    const router = mount('/decks');
    await openMenu(user);
    await user.click(screen.getByRole('button', { name: 'Sign out everywhere' }));
    const dialog = screen.getByRole('dialog', { name: 'Sign out everywhere?' });
    expect(api.count('/api/v1/auth/logout-all')).toBe(0);
    await user.click(within(dialog).getByRole('button', { name: 'Sign out everywhere' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/sign-in'));
    expect(api.calls.filter((c) => c.path === '/api/v1/auth/logout-all')).toEqual([
      { path: '/api/v1/auth/logout-all', method: 'POST' },
    ]);
    expect(useSessionStore.getState().accessToken).toBeNull();
    expect(useSessionStore.getState().status).toBe('signed_out');
  });

  it('calls nothing when the Cancel button is pressed', async () => {
    const api = stubApi();
    const user = userEvent.setup();
    const router = mount('/decks');
    await openMenu(user);
    await user.click(screen.getByRole('button', { name: 'Sign out everywhere' }));
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(api.count('/api/v1/auth/logout-all')).toBe(0);
    expect(api.count('/api/v1/auth/logout')).toBe(0);
    expect(useSessionStore.getState().status).toBe('signed_in');
    expect(router.state.location.pathname).toBe('/decks');
  });

  it('calls nothing when Escape is pressed', async () => {
    const api = stubApi();
    const user = userEvent.setup();
    const router = mount('/decks');
    await openMenu(user);
    await user.click(screen.getByRole('button', { name: 'Sign out everywhere' }));
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(api.calls.filter((c) => c.path.includes('/auth/logout'))).toHaveLength(0);
    expect(useSessionStore.getState().status).toBe('signed_in');
    expect(router.state.location.pathname).toBe('/decks');
  });

  // The implementation chose "stay signed in and show an alert" when logout-all fails.
  it.each<Mode>(['500', 'network'])(
    'keeps the user signed in and shows an alert when logout-all fails (%s)',
    async (mode) => {
      stubApi({ logoutAll: mode });
      const user = userEvent.setup();
      const router = mount('/decks');
      await openMenu(user);
      await user.click(screen.getByRole('button', { name: 'Sign out everywhere' }));
      const dialog = screen.getByRole('dialog');
      await user.click(within(dialog).getByRole('button', { name: 'Sign out everywhere' }));
      expect(await within(dialog).findByRole('alert')).toBeInTheDocument();
      expect(useSessionStore.getState().status).toBe('signed_in');
      expect(useSessionStore.getState().accessToken).toBe(ACCESS);
      expect(router.state.location.pathname).toBe('/decks');
      expect(FakeBroadcastChannel.posts).toHaveLength(0);
    },
  );
});

describe('Session expired', () => {
  it('redirects to /sign-in?next=<path+search+hash> and shows the expired message', async () => {
    stubApi();
    const router = mount('/decks?tab=a&x=1#top');
    handleSessionExpired();
    await waitFor(() => expect(router.state.location.pathname).toBe('/sign-in'));
    expect(new URLSearchParams(router.state.location.search).get('next')).toBe(
      '/decks?tab=a&x=1#top',
    );
    expect(await screen.findByText('Your session has expired')).toBeInTheDocument();
    expect(useSessionStore.getState().accessToken).toBeNull();
  });

  it('returns the user to next after signing in again', async () => {
    stubApi();
    const user = userEvent.setup();
    const router = mount('/stats?range=30d#top');
    handleSessionExpired();
    await screen.findByText('Your session has expired');
    await user.type(screen.getByLabelText(/username/i), 'ada');
    await user.type(screen.getByLabelText(/password/i), 'correct horse battery');
    await user.click(screen.getByRole('button', { name: /sign in/i }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/stats'));
    expect(router.state.location.search).toBe('?range=30d');
    expect(router.state.location.hash).toBe('#top');
    expect(screen.queryByText('Your session has expired')).not.toBeInTheDocument();
  });

  it.each(['//evil.example', 'https://evil.example/x', '/\\evil.example'])(
    'ignores an off-origin next (%s) after signing in',
    async (raw) => {
      stubApi();
      useSessionStore.setState({
        status: 'signed_out',
        accessToken: null,
        signOutReason: 'expired',
      });
      const user = userEvent.setup();
      const router = mount(`/sign-in?next=${encodeURIComponent(raw)}`);
      expect(screen.getByText('Your session has expired')).toBeInTheDocument();
      await user.type(screen.getByLabelText(/username/i), 'ada');
      await user.type(screen.getByLabelText(/password/i), 'correct horse battery');
      await user.click(screen.getByRole('button', { name: /sign in/i }));
      await waitFor(() => expect(router.state.location.pathname).toBe('/'));
      expect(router.state.location.search).toBe('');
    },
  );

  it('shows no expired message to a first-time visitor', () => {
    useSessionStore.setState({ status: 'signed_out', accessToken: null, signOutReason: null });
    const router = mount('/decks');
    expect(router.state.location.pathname).toBe('/sign-in');
    expect(screen.queryByText(/session has expired/i)).not.toBeInTheDocument();
  });

  it('shows no expired message when the startup restore fails', async () => {
    useSessionStore.setState({ status: 'restoring', accessToken: null, signOutReason: null });
    const router = mount('/decks');
    handleSessionExpired(); // the client reports the failed refresh while restoring
    await waitFor(() => expect(router.state.location.pathname).toBe('/sign-in'));
    expect(screen.queryByText(/session has expired/i)).not.toBeInTheDocument();
  });
});

describe('Keyboard', () => {
  async function openDialog(user: User) {
    await openMenu(user);
    await user.click(screen.getByRole('button', { name: 'Sign out everywhere' }));
    return screen.getByRole('dialog');
  }

  it('opens the menu with Enter, reaches both actions with Tab, and closes with Escape', async () => {
    stubApi();
    const user = userEvent.setup();
    mount('/decks');
    const toggle = screen.getByRole('button', { name: 'Account menu' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    toggle.focus();
    await user.keyboard('{Enter}');
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await user.tab();
    expect(screen.getByRole('button', { name: 'Sign out' })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole('button', { name: 'Sign out everywhere' })).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('button', { name: 'Sign out' })).not.toBeInTheDocument();
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(toggle).toHaveFocus();
  });

  it('signs out with Enter on the Sign out action', async () => {
    const api = stubApi();
    const user = userEvent.setup();
    const router = mount('/decks');
    screen.getByRole('button', { name: 'Account menu' }).focus();
    await user.keyboard('{Enter}');
    await user.tab();
    await user.keyboard('{Enter}');
    await waitFor(() => expect(router.state.location.pathname).toBe('/sign-in'));
    expect(api.count('/api/v1/auth/logout')).toBe(1);
  });

  it('runs sign out everywhere end to end by keyboard', async () => {
    const api = stubApi();
    const user = userEvent.setup();
    const router = mount('/decks');
    screen.getByRole('button', { name: 'Account menu' }).focus();
    await user.keyboard('{Enter}');
    await user.tab();
    await user.tab();
    await user.keyboard('{Enter}');
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus();
    await user.tab();
    expect(within(dialog).getByRole('button', { name: 'Sign out everywhere' })).toHaveFocus();
    await user.keyboard('{Enter}');
    await waitFor(() => expect(router.state.location.pathname).toBe('/sign-in'));
    expect(api.count('/api/v1/auth/logout-all')).toBe(1);
  });

  it('declares a modal dialog and focuses Cancel on open', async () => {
    stubApi();
    const user = userEvent.setup();
    mount('/decks');
    const dialog = await openDialog(user);
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus();
  });

  // jsdom has no top layer, no inert background and no real Tab trap (setup.ts only toggles
  // `open`); the real Tab / Shift+Tab trap is covered by the Playwright e2e suite. What jsdom can
  // check is asserted here.
  it('is a native <dialog> opened modally', async () => {
    stubApi();
    const user = userEvent.setup();
    mount('/decks');
    const dialog = await openDialog(user);
    expect(dialog.tagName).toBe('DIALOG');
    expect(dialog).toHaveAttribute('open');
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(dialog).toHaveAccessibleName('Sign out everywhere?');
    expect(dialog.parentElement).toBe(document.body); // portal, so no hidden ancestor
  });

  it('calls cancel and returns focus to the opener on the native cancel event', async () => {
    const api = stubApi();
    const user = userEvent.setup();
    mount('/decks');
    const dialog = await openDialog(user);
    const event = new Event('cancel', { cancelable: true });
    act(() => {
      dialog.dispatchEvent(event);
    });
    expect(event.defaultPrevented).toBe(true); // the owner decides; no native close
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Account menu' })).toHaveFocus();
    expect(api.calls.filter((c) => c.path.includes('/auth/logout'))).toHaveLength(0);
    expect(useSessionStore.getState().status).toBe('signed_in');
  });

  it('returns focus to the opener when Cancel is pressed', async () => {
    stubApi();
    const user = userEvent.setup();
    mount('/decks');
    const dialog = await openDialog(user);
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Account menu' })).toHaveFocus();
  });

  it('returns focus to the account menu button when the dialog closes', async () => {
    stubApi();
    const user = userEvent.setup();
    mount('/decks');
    await openDialog(user);
    await user.keyboard('{Escape}');
    expect(screen.getByRole('button', { name: 'Account menu' })).toHaveFocus();
  });
});

describe('User state registry', () => {
  it('runs a registered reset on sign out', async () => {
    stubApi();
    const reset = vi.fn();
    const unregister = registerUserStateReset(reset);
    const user = userEvent.setup();
    mount('/decks');
    await openMenu(user);
    await user.click(screen.getByRole('button', { name: 'Sign out' }));
    await waitFor(() => expect(reset).toHaveBeenCalled());
    unregister();
  });

  it('runs every reset even when one throws', () => {
    const before = vi.fn();
    const after = vi.fn();
    const offs = [
      registerUserStateReset(before),
      registerUserStateReset(() => {
        throw new Error('broken reset');
      }),
      registerUserStateReset(after),
    ];
    expect(() => {
      resetUserState();
    }).not.toThrow();
    expect(before).toHaveBeenCalledTimes(1);
    expect(after).toHaveBeenCalledTimes(1);
    for (const off of offs) off();
  });

  it('stops running a reset once it is unregistered', () => {
    const reset = vi.fn();
    const off = registerUserStateReset(reset);
    resetUserState();
    off();
    resetUserState();
    expect(reset).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------------------------
// Review round: pending-logout flag, "this device only", abort/timeout, races, locks, wiring.
// ---------------------------------------------------------------------------------------------

const LATE = 'late-access-token'; // secrets-check: allow
const LOGOUT = '/api/v1/auth/logout';
const LOGOUT_ALL = '/api/v1/auth/logout-all';
const REFRESH = '/api/v1/auth/refresh';
const flag = () => localStorage.getItem(LOGOUT_PENDING_KEY);

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

const signOutFromMenu = async (user: User) => {
  await openMenu(user);
  await user.click(screen.getByRole('button', { name: 'Sign out' }));
};

const openEverywhereDialog = async (user: User) => {
  await openMenu(user);
  await user.click(screen.getByRole('button', { name: 'Sign out everywhere' }));
  return screen.getByRole('dialog');
};

const warning = () => screen.findByText(LOGOUT_PENDING_WARNING);

describe('Pending logout: the server call fails', () => {
  it.each<Mode>(['network', '500'])(
    'signs out locally, sets the pending flag and warns when POST /auth/logout fails (%s)',
    async (mode) => {
      stubApi({ logout: mode });
      const user = userEvent.setup();
      const router = mount('/decks');
      await signOutFromMenu(user);
      await waitFor(() => expect(router.state.location.pathname).toBe('/sign-in'));
      expect(useSessionStore.getState().status).toBe('signed_out');
      expect(useSessionStore.getState().accessToken).toBeNull();
      expect(flag()).not.toBeNull();
      // The toast region is always in the DOM, so wait for the message and check it sits in it.
      expect((await warning()).closest('[role="alert"]')).not.toBeNull();
    },
  );

  it('signs out locally, sets the flag and warns when POST /auth/logout times out after 4 s', async () => {
    const api = stubApi({ logout: 'hang' });
    const user = useFakeClock();
    const router = mount('/decks');
    await signOutFromMenu(user);
    await waitFor(() => expect(api.count(LOGOUT)).toBe(1));
    expect(useSessionStore.getState().status).toBe('signed_in'); // still waiting for the server
    await pastTimeout();
    await waitFor(() => expect(router.state.location.pathname).toBe('/sign-in'));
    expect(api.aborted).toEqual([LOGOUT]);
    expect(useSessionStore.getState().status).toBe('signed_out');
    expect(flag()).not.toBeNull();
    expect(await warning()).toBeInTheDocument();
  });

  it('leaves no flag and no warning after a successful logout', async () => {
    stubApi();
    const user = userEvent.setup();
    const router = mount('/decks');
    await signOutFromMenu(user);
    await waitFor(() => expect(router.state.location.pathname).toBe('/sign-in'));
    await screen.findByRole('heading', { level: 2, name: 'Sign in' });
    expect(flag()).toBeNull();
    expect(screen.queryByText(LOGOUT_PENDING_WARNING)).not.toBeInTheDocument();
  });

  it('stores no token in Web Storage (the flag is only a marker)', async () => {
    stubApi({ logout: 'network' });
    const user = userEvent.setup();
    const router = mount('/decks');
    await signOutFromMenu(user);
    await waitFor(() => expect(router.state.location.pathname).toBe('/sign-in'));
    expect(flag()).not.toBeNull();
    expect(flag()).not.toContain(ACCESS);
    const everything = Object.keys(localStorage).map((k) => `${k}=${localStorage.getItem(k)}`);
    expect(JSON.stringify(everything)).not.toContain(ACCESS);
    expect(Object.keys(sessionStorage)).toHaveLength(0);
  });

  it('still signs out and warns when localStorage throws on write', async () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    stubApi({ logout: '500' });
    const user = userEvent.setup();
    const router = mount('/decks');
    await signOutFromMenu(user);
    await waitFor(() => expect(router.state.location.pathname).toBe('/sign-in'));
    expect(useSessionStore.getState().status).toBe('signed_out');
    expect(await warning()).toBeInTheDocument();
  });

  it('still signs out and warns when localStorage is unavailable', async () => {
    const original = globalThis.localStorage;
    stubApi({ logout: 'network' });
    const user = userEvent.setup();
    const router = mount('/decks');
    vi.stubGlobal('localStorage', undefined);
    try {
      await signOutFromMenu(user);
      await waitFor(() => expect(router.state.location.pathname).toBe('/sign-in'));
      expect(useSessionStore.getState().status).toBe('signed_out');
      expect(await warning()).toBeInTheDocument();
    } finally {
      vi.stubGlobal('localStorage', original);
    }
  });
});

describe('Pending logout: the next load', () => {
  beforeEach(() => {
    useSessionStore.setState({ status: 'restoring', accessToken: null, signOutReason: null });
  });

  it('calls POST /auth/logout instead of refresh and stays signed out', async () => {
    localStorage.setItem(LOGOUT_PENDING_KEY, '1');
    const seen = stubApi({
      refresh: () => json({ access_token: LATE, token_type: 'bearer', expires_in: 900 }),
    });
    await restoreSession(api);
    expect(seen.count(LOGOUT)).toBe(1);
    expect(seen.count(REFRESH)).toBe(0);
    expect(useSessionStore.getState().status).toBe('signed_out');
    expect(useSessionStore.getState().accessToken).toBeNull();
  });

  it('clears the flag when that call succeeds', async () => {
    localStorage.setItem(LOGOUT_PENDING_KEY, '1');
    stubApi({ logout: 'ok' });
    await restoreSession(api);
    expect(flag()).toBeNull();
  });

  it.each<Mode>(['network', '500'])(
    'keeps the flag when that call fails (%s) and retries on a later load',
    async (mode) => {
      localStorage.setItem(LOGOUT_PENDING_KEY, '1');
      const first = stubApi({ logout: mode });
      await restoreSession(api);
      expect(first.count(LOGOUT)).toBe(1);
      expect(first.count(REFRESH)).toBe(0);
      expect(flag()).not.toBeNull();
      expect(useSessionStore.getState().status).toBe('signed_out');

      // A later load: the same flag, a server that answers this time.
      useSessionStore.setState({ status: 'restoring', accessToken: null, signOutReason: null });
      const second = stubApi({ logout: 'ok' });
      await restoreSession(api);
      expect(second.count(LOGOUT)).toBe(1);
      expect(second.count(REFRESH)).toBe(0);
      expect(flag()).toBeNull();
      expect(useSessionStore.getState().status).toBe('signed_out');
    },
  );

  it('keeps the flag when the retry times out after 4 s', async () => {
    localStorage.setItem(LOGOUT_PENDING_KEY, '1');
    const seen = stubApi({ logout: 'hang' });
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'], shouldAdvanceTime: true });
    const done = restoreSession(api);
    await waitFor(() => expect(seen.count(LOGOUT)).toBe(1));
    await pastTimeout();
    await done;
    expect(flag()).not.toBeNull();
    expect(useSessionStore.getState().status).toBe('signed_out');
  });

  it('refreshes as usual when no flag is set', async () => {
    const seen = stubApi();
    await restoreSession(api);
    expect(seen.count(REFRESH)).toBe(1);
    expect(seen.count(LOGOUT)).toBe(0);
  });

  it('refreshes as usual and does not throw when localStorage throws on read', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    const seen = stubApi();
    await expect(restoreSession(api)).resolves.toBeUndefined();
    expect(seen.count(REFRESH)).toBe(1);
    expect(useSessionStore.getState().status).toBe('signed_out');
  });

  it('refreshes as usual when localStorage is unavailable', async () => {
    const original = globalThis.localStorage;
    vi.stubGlobal('localStorage', undefined);
    try {
      const seen = stubApi();
      await expect(restoreSession(api)).resolves.toBeUndefined();
      expect(seen.count(REFRESH)).toBe(1);
    } finally {
      vi.stubGlobal('localStorage', original);
    }
  });
});

describe('Sign out everywhere: failure, "this device only", cancel and timeout', () => {
  it('shows the error, moves focus to the alert and offers "Sign out on this device only"', async () => {
    stubApi({ logoutAll: '500' });
    const user = userEvent.setup();
    mount('/decks');
    const dialog = await openEverywhereDialog(user);
    expect(
      within(dialog).queryByRole('button', { name: 'Sign out on this device only' }),
    ).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Sign out everywhere' }));
    const alert = await within(dialog).findByRole('alert');
    expect(alert).toHaveTextContent(SIGN_OUT_EVERYWHERE_FAILED);
    await waitFor(() => expect(alert).toHaveFocus());
    expect(
      within(dialog).getByRole('button', { name: 'Sign out on this device only' }),
    ).toBeInTheDocument();
  });

  it('"Sign out on this device only" signs out locally through POST /auth/logout', async () => {
    const api = stubApi({ logoutAll: 'network' });
    const user = userEvent.setup();
    const router = mount('/decks');
    const dialog = await openEverywhereDialog(user);
    await user.click(within(dialog).getByRole('button', { name: 'Sign out everywhere' }));
    await within(dialog).findByRole('alert');
    await user.click(within(dialog).getByRole('button', { name: 'Sign out on this device only' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/sign-in'));
    expect(useSessionStore.getState().status).toBe('signed_out');
    expect(useSessionStore.getState().accessToken).toBeNull();
    expect(api.count(LOGOUT)).toBe(1);
    expect(flag()).toBeNull();
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('"Sign out on this device only" sets the pending flag when /auth/logout fails too', async () => {
    stubApi({ logoutAll: '500', logout: '500' });
    const user = userEvent.setup();
    const router = mount('/decks');
    const dialog = await openEverywhereDialog(user);
    await user.click(within(dialog).getByRole('button', { name: 'Sign out everywhere' }));
    await within(dialog).findByRole('alert');
    await user.click(within(dialog).getByRole('button', { name: 'Sign out on this device only' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/sign-in'));
    expect(useSessionStore.getState().status).toBe('signed_out');
    expect(flag()).not.toBeNull();
    expect(await warning()).toBeInTheDocument();
  });

  it('keeps the confirm button focused and aria-disabled (not disabled) while pending, and sends one request', async () => {
    const api = stubApi({ logoutAll: 'hang' });
    const user = userEvent.setup();
    mount('/decks');
    const dialog = await openEverywhereDialog(user);
    const confirm = within(dialog).getByRole('button', { name: 'Sign out everywhere' });
    await user.click(confirm);
    await waitFor(() => expect(api.count(LOGOUT_ALL)).toBe(1));
    const pending = within(dialog).getByRole('button', { name: /signing out/i });
    expect(pending).toHaveAttribute('aria-disabled', 'true');
    expect(pending).not.toBeDisabled();
    expect(pending).toHaveFocus();
    await user.click(pending);
    expect(api.count(LOGOUT_ALL)).toBe(1);
    expect(useSessionStore.getState().status).toBe('signed_in');
  });

  it('aborts the request and closes the dialog when Cancel is pressed while pending', async () => {
    const api = stubApi({ logoutAll: 'hang' });
    const user = userEvent.setup();
    const router = mount('/decks');
    const dialog = await openEverywhereDialog(user);
    await user.click(within(dialog).getByRole('button', { name: 'Sign out everywhere' }));
    await waitFor(() => expect(api.count(LOGOUT_ALL)).toBe(1));
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(api.aborted).toEqual([LOGOUT_ALL]));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.queryByText(SIGN_OUT_EVERYWHERE_FAILED)).not.toBeInTheDocument();
    expect(useToastStore.getState().toasts).toEqual([]);
    expect(useSessionStore.getState().status).toBe('signed_in');
    expect(router.state.location.pathname).toBe('/decks');
    expect(api.count(LOGOUT)).toBe(0);
    expect(screen.getByRole('button', { name: 'Account menu' })).toHaveFocus();
  });

  it('aborts the request and closes the dialog when Escape is pressed while pending', async () => {
    const api = stubApi({ logoutAll: 'hang' });
    const user = userEvent.setup();
    mount('/decks');
    const dialog = await openEverywhereDialog(user);
    await user.click(within(dialog).getByRole('button', { name: 'Sign out everywhere' }));
    await waitFor(() => expect(api.count(LOGOUT_ALL)).toBe(1));
    await user.keyboard('{Escape}');
    await waitFor(() => expect(api.aborted).toEqual([LOGOUT_ALL]));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.queryByText(SIGN_OUT_EVERYWHERE_FAILED)).not.toBeInTheDocument();
    expect(useToastStore.getState().toasts).toEqual([]);
    expect(useSessionStore.getState().status).toBe('signed_in');
    expect(api.count(LOGOUT)).toBe(0);
  });

  it('ends in the error state, not stuck, when logout-all hangs for 4 s', async () => {
    const api = stubApi({ logoutAll: 'hang' });
    const user = useFakeClock();
    mount('/decks');
    const dialog = await openEverywhereDialog(user);
    await user.click(within(dialog).getByRole('button', { name: 'Sign out everywhere' }));
    await waitFor(() => expect(api.count(LOGOUT_ALL)).toBe(1));
    expect(within(dialog).queryByRole('alert')).not.toBeInTheDocument(); // not before the limit
    await pastTimeout();
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(SIGN_OUT_EVERYWHERE_FAILED);
    expect(api.aborted).toEqual([LOGOUT_ALL]);
    expect(useSessionStore.getState().status).toBe('signed_in');
    const confirm = within(dialog).getByRole('button', { name: 'Sign out everywhere' });
    expect(confirm).toHaveAttribute('aria-disabled', 'false'); // usable again
    expect(
      within(dialog).getByRole('button', { name: 'Sign out on this device only' }),
    ).toBeInTheDocument();
  });
});

describe('A refresh that finishes after sign-out', () => {
  it('setTokens is ignored while signed_out and applied otherwise', () => {
    useSessionStore.setState({ status: 'signed_out', accessToken: null });
    void memoryTokenStore.setTokens({ accessToken: LATE });
    expect(useSessionStore.getState().accessToken).toBeNull();
    expect(useSessionStore.getState().status).toBe('signed_out');

    useSessionStore.setState({ status: 'restoring', accessToken: null });
    void memoryTokenStore.setTokens({ accessToken: LATE });
    expect(useSessionStore.getState().accessToken).toBe(LATE);

    useSessionStore.setState({ status: 'signed_in', accessToken: ACCESS });
    void memoryTokenStore.setTokens({ accessToken: 'rotated-access' }); // secrets-check: allow
    expect(useSessionStore.getState().accessToken).toBe('rotated-access'); // secrets-check: allow
  });

  it('through the real client: 401, refresh in flight, user signs out, refresh resolves: still signed out', async () => {
    let release: (response: Response) => void = () => undefined;
    const gate = new Promise<Response>((resolve) => {
      release = resolve;
    });
    const seen = stubApi({ refresh: () => gate });
    const user = userEvent.setup();
    const router = mount('/decks');
    const outcome = api.GET('/api/v1/auth/me').then(
      () => 'ok',
      () => 'rejected',
    );
    await waitFor(() => expect(seen.count(REFRESH)).toBe(1));
    await signOutFromMenu(user);
    await waitFor(() => expect(router.state.location.pathname).toBe('/sign-in'));
    expect(useSessionStore.getState().status).toBe('signed_out');

    release(json({ access_token: LATE, token_type: 'bearer', expires_in: 900 }));
    await outcome;
    expect(useSessionStore.getState().status).toBe('signed_out');
    expect(useSessionStore.getState().accessToken).toBeNull();
    expect(router.state.location.pathname).toBe('/sign-in');
  });
});

describe('Session expiry through the real client wiring (src/api.ts)', () => {
  it('a protected query gets 401 and the refresh gets 401: /sign-in?next=... and the expired message', async () => {
    const seen = stubApi();
    const router = mount('/decks?tab=a#top');
    await expect(api.GET('/api/v1/auth/me')).rejects.toBeDefined();
    expect(seen.count(REFRESH)).toBe(1);
    await waitFor(() => expect(router.state.location.pathname).toBe('/sign-in'));
    expect(new URLSearchParams(router.state.location.search).get('next')).toBe('/decks?tab=a#top');
    expect(await screen.findByText('Your session has expired')).toBeInTheDocument();
    expect(useSessionStore.getState().status).toBe('signed_out');
    expect(useSessionStore.getState().signOutReason).toBe('expired');
    expect(useSessionStore.getState().accessToken).toBeNull();
  });
});

describe('handleSessionExpired when already signed out', () => {
  it.each(['expired', 'user'] as const)('preserves the "%s" reason and runs no reset', (reason) => {
    const reset = vi.fn();
    const off = registerUserStateReset(reset);
    useSessionStore.setState({ status: 'signed_out', accessToken: null, signOutReason: reason });
    handleSessionExpired();
    expect(useSessionStore.getState().signOutReason).toBe(reason);
    expect(useSessionStore.getState().status).toBe('signed_out');
    expect(reset).not.toHaveBeenCalled();
    off();
  });

  it('keeps a null reason null', () => {
    useSessionStore.setState({ status: 'signed_out', accessToken: null, signOutReason: null });
    handleSessionExpired();
    expect(useSessionStore.getState().signOutReason).toBeNull();
  });
});

describe('Logout and the refresh lock', () => {
  const lockHolder = () => {
    const names: string[] = [];
    let held = false;
    Object.defineProperty(navigator, 'locks', {
      configurable: true,
      value: {
        request: vi.fn(
          async (name: string, _options: unknown, callback: () => Promise<unknown>) => {
            names.push(name);
            held = true;
            try {
              return await callback();
            } finally {
              held = false;
            }
          },
        ),
      },
    });
    return { names, isHeld: () => held };
  };

  it('runs POST /auth/logout inside navigator.locks under the refresh lock name', async () => {
    const lock = lockHolder();
    try {
      const heldAt: Record<string, boolean> = {};
      const seen = stubApi({
        onCall: (path) => {
          heldAt[path] = lock.isHeld();
        },
      });
      const user = userEvent.setup();
      const router = mount('/decks');
      await signOutFromMenu(user);
      await waitFor(() => expect(router.state.location.pathname).toBe('/sign-in'));
      expect(REFRESH_LOCK).toBe('mtgstats-auth-refresh');
      expect(lock.names).toEqual([REFRESH_LOCK]);
      expect(seen.count(LOGOUT)).toBe(1);
      expect(heldAt[LOGOUT]).toBe(true);
    } finally {
      Reflect.deleteProperty(navigator, 'locks');
    }
  });

  it('falls back to a plain call when navigator.locks is missing', async () => {
    expect((navigator as { locks?: unknown }).locks).toBeUndefined();
    const seen = stubApi();
    const user = userEvent.setup();
    const router = mount('/decks');
    await signOutFromMenu(user);
    await waitFor(() => expect(router.state.location.pathname).toBe('/sign-in'));
    expect(seen.count(LOGOUT)).toBe(1);
    expect(flag()).toBeNull();
  });
});

describe('Toasts and tab sync on sign-out', () => {
  it('clears every toast on sign-out (registered reset)', () => {
    useToastStore.getState().push('info', 'Deck saved');
    useToastStore.getState().push('error', 'Something failed');
    expect(useToastStore.getState().toasts).toHaveLength(2);
    clearLocalSession('user');
    expect(useToastStore.getState().toasts).toEqual([]);
  });

  it('clears toasts when the user signs out from the menu', async () => {
    stubApi();
    useToastStore.getState().push('error', 'Private deck name failed to save');
    const user = userEvent.setup();
    const router = mount('/decks');
    await signOutFromMenu(user);
    await waitFor(() => expect(router.state.location.pathname).toBe('/sign-in'));
    expect(useToastStore.getState().toasts).toEqual([]);
    expect(screen.queryByText('Private deck name failed to save')).not.toBeInTheDocument();
  });

  it('ignores the broadcast while the tab is still restoring (keeps the deep link)', async () => {
    useSessionStore.setState({ status: 'restoring', accessToken: null, signOutReason: null });
    const router = mount('/decks?tab=a');
    const otherTab = new FakeBroadcastChannel(SIGN_OUT_CHANNEL);
    otherTab.postMessage({ type: 'signed_out' });
    await new Promise((r) => setTimeout(r, 50));
    expect(useSessionStore.getState().status).toBe('restoring');
    expect(useSessionStore.getState().signOutReason).toBeNull();
    expect(router.state.location.pathname).toBe('/decks');
    expect(router.state.location.search).toBe('?tab=a');
    otherTab.close();
  });

  it('ignores the broadcast while the tab is already signed out (reason kept)', async () => {
    useSessionStore.setState({ status: 'signed_out', accessToken: null, signOutReason: 'expired' });
    const otherTab = new FakeBroadcastChannel(SIGN_OUT_CHANNEL);
    otherTab.postMessage({ type: 'signed_out' });
    await new Promise((r) => setTimeout(r, 50));
    expect(useSessionStore.getState().signOutReason).toBe('expired');
    otherTab.close();
  });
});

describe('Account menu', () => {
  const nav = () => screen.getByRole('navigation', { name: 'Primary' });

  it('Escape inside the account menu closes only the menu, not the compact nav', async () => {
    stubApi();
    const user = userEvent.setup();
    mount('/decks');
    await user.click(screen.getByRole('button', { name: 'Menu' }));
    expect(nav()).toHaveAttribute('data-open', 'true');
    await openMenu(user);
    const toggle = screen.getByRole('button', { name: 'Account menu' });
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await user.keyboard('{Escape}');
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(toggle).toHaveFocus();
    expect(nav()).toHaveAttribute('data-open', 'true');
  });

  it('Escape with the account menu closed still closes the compact nav', async () => {
    stubApi();
    const user = userEvent.setup();
    mount('/decks');
    await user.click(screen.getByRole('button', { name: 'Menu' }));
    screen.getByRole('button', { name: 'Account menu' }).focus();
    await user.keyboard('{Escape}');
    expect(nav()).toHaveAttribute('data-open', 'false');
  });

  it('sets aria-controls only while the menu is open, pointing at the list', async () => {
    stubApi();
    const user = userEvent.setup();
    mount('/decks');
    const toggle = screen.getByRole('button', { name: 'Account menu' });
    expect(toggle).not.toHaveAttribute('aria-controls');
    await user.click(toggle);
    const id = toggle.getAttribute('aria-controls');
    expect(id).toBeTruthy();
    const list = document.getElementById(id ?? '');
    expect(list).toBeInTheDocument();
    expect(list).toContainElement(screen.getByRole('button', { name: 'Sign out' }));
    await user.click(toggle);
    expect(toggle).not.toHaveAttribute('aria-controls');
    expect(document.getElementById(id ?? '')).toBeNull();
  });
});
