// S-2.1.12 (mobile): signOut() semantics (bounded best-effort call), the store registry, and the
// query cache being cleared on sign-out.
import { createQueryClient } from '@mtg-stats/api-client';
import type { ApiClient } from '@mtg-stats/api-client';
import { render } from '@testing-library/react-native';
import { AppProviders } from '../components/AppProviders';
import { useSessionStore } from '../session/store';
import { signOut } from '../session/signOut';
import { registerStoreReset, resetUserStores } from '../session/storeRegistry';

jest.mock('expo-secure-store', () =>
  jest.requireActual<typeof import('./app-harness')>('./app-harness').createSecureStoreMock(),
);

const REFRESH_TOKEN = 'unit-refresh-token'; // secrets-check: allow

async function signedIn() {
  await useSessionStore.getState().signIn({
    accessToken: 'unit-access-token', // secrets-check: allow
    refreshToken: REFRESH_TOKEN,
  });
}

afterEach(async () => {
  jest.useRealTimers();
  await useSessionStore.getState().signOut();
});

describe('store registry', () => {
  it('runs every registered reset on resetUserStores', () => {
    const a = jest.fn();
    const b = jest.fn();
    const offA = registerStoreReset(a);
    const offB = registerStoreReset(b);
    resetUserStores();
    expect(a).toHaveBeenCalledTimes(1);
    expect(b).toHaveBeenCalledTimes(1);
    offA();
    offB();
  });

  it('keeps running the other resets when one throws, and does not throw itself', () => {
    const after = jest.fn();
    const off1 = registerStoreReset(() => {
      throw new Error('boom');
    });
    const off2 = registerStoreReset(after);
    expect(() => resetUserStores()).not.toThrow();
    expect(after).toHaveBeenCalledTimes(1);
    off1();
    off2();
  });

  it('stops running a reset once it is unregistered', () => {
    const reset = jest.fn();
    const off = registerStoreReset(reset);
    off();
    resetUserStores();
    expect(reset).not.toHaveBeenCalled();
  });

  it('runs the registered resets as part of signOut', async () => {
    const reset = jest.fn();
    const off = registerStoreReset(reset);
    await signedIn();
    await signOut({ client: null });
    expect(reset).toHaveBeenCalledTimes(1);
    off();
  });
});

describe('signOut with an injected client', () => {
  it('posts /auth/logout with the refresh token, then clears the session', async () => {
    const post = jest.fn().mockResolvedValue({ data: undefined });
    await signedIn();
    await signOut({ client: { POST: post } as unknown as ApiClient });
    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0]![0]).toBe('/api/v1/auth/logout');
    expect(post.mock.calls[0]![1].body).toEqual({ refresh_token: REFRESH_TOKEN });
    expect(useSessionStore.getState().status).toBe('signed-out');
    expect(useSessionStore.getState().refreshToken).toBeNull();
  });

  it('does not wait forever on a hung request: it aborts and signs out within the bound', async () => {
    jest.useFakeTimers();
    const post = jest.fn(
      (_path: string, init: { signal: AbortSignal }) =>
        new Promise((_resolve, reject) => {
          init.signal.addEventListener('abort', () => reject(new Error('aborted')));
        }),
    );
    await signedIn();
    let done = false;
    const pending = signOut({ client: { POST: post } as unknown as ApiClient }).then(() => {
      done = true;
    });
    await jest.advanceTimersByTimeAsync(100);
    expect(done).toBe(false);
    await jest.advanceTimersByTimeAsync(10000);
    await pending;
    expect(done).toBe(true);
    expect(useSessionStore.getState().status).toBe('signed-out');
  });

  it('signs out locally when the call rejects', async () => {
    const post = jest.fn().mockRejectedValue(new TypeError('Network request failed'));
    await signedIn();
    await expect(
      signOut({ client: { POST: post } as unknown as ApiClient }),
    ).resolves.toBeUndefined();
    expect(useSessionStore.getState().status).toBe('signed-out');
  });
});

describe('query cache', () => {
  it('is cleared on sign-out', async () => {
    const queryClient = createQueryClient();
    queryClient.setQueryData(['me'], { name: 'ada' });
    await render(<AppProviders queryClient={queryClient}>{null}</AppProviders>);
    await signedIn();
    await signOut({ client: null });
    expect(queryClient.getQueryData(['me'])).toBeUndefined();
    expect(queryClient.getQueryCache().getAll()).toHaveLength(0);
  });
});
