// S-2.1.7 decisions 3 and 5, and AC1 "never in plain async storage": the secure session store and
// its token store, against an in-memory expo-secure-store.
import fs from 'node:fs';
import path from 'node:path';
import type { TokenStore } from '@mtg-stats/api-client';
import { ACCESS_1, ACCESS_2, REFRESH_1, REFRESH_2, sourceFiles } from './app-harness';
import type { SecureStoreMock } from './app-harness';

jest.mock('expo-secure-store', () =>
  jest.requireActual<typeof import('./app-harness')>('./app-harness').createSecureStoreMock(),
);

interface SessionState {
  status: 'restoring' | 'signed-in' | 'signed-out';
  accessToken: string | null;
  refreshToken: string | null;
  signIn: (tokens: { accessToken: string; refreshToken: string }) => unknown;
  signOut: () => unknown;
}
type Hook = { getState: () => SessionState };

function load() {
  jest.resetModules();
  const secureStore = jest.requireMock<SecureStoreMock>('expo-secure-store');
  const secureTokenStore: TokenStore = jest.requireActual<
    typeof import('../session/secureTokenStore')
  >('../session/secureTokenStore').secureTokenStore;
  const useSessionStore: Hook =
    jest.requireActual<typeof import('../session/store')>('../session/store').useSessionStore;
  return { secureStore, secureTokenStore, useSessionStore };
}

/** Lets pending promise continuations (a swallowed secure-store failure) settle. */
const flush = async () => {
  for (let i = 0; i < 5; i += 1) await Promise.resolve();
};

describe('secureTokenStore (body mode)', () => {
  it('reads tokens from memory, never from the secure store', async () => {
    const { secureStore, secureTokenStore } = load();
    await secureTokenStore.setTokens({ accessToken: ACCESS_1, refreshToken: REFRESH_1 });
    secureStore.getItemAsync.mockClear();
    expect(await secureTokenStore.getAccessToken()).toBe(ACCESS_1);
    expect(await secureTokenStore.getRefreshToken?.()).toBe(REFRESH_1);
    expect(secureStore.getItemAsync).not.toHaveBeenCalled();
  });

  it('setTokens updates memory and writes both tokens under separate keys', async () => {
    const { secureStore, secureTokenStore } = load();
    await secureTokenStore.setTokens({ accessToken: ACCESS_2, refreshToken: REFRESH_2 });
    const writes = secureStore.setItemAsync.mock.calls;
    const access = writes.find(([, v]) => v === ACCESS_2);
    const refresh = writes.find(([, v]) => v === REFRESH_2);
    expect(access![0]).toMatch(/access/i);
    expect(refresh![0]).toMatch(/refresh/i);
    expect(access![0]).not.toBe(refresh![0]);
    expect(await secureTokenStore.getAccessToken()).toBe(ACCESS_2);
  });

  it('clear nulls memory and deletes both keys', async () => {
    const { secureStore, secureTokenStore } = load();
    await secureTokenStore.setTokens({ accessToken: ACCESS_1, refreshToken: REFRESH_1 });
    await secureTokenStore.clear();
    expect(await secureTokenStore.getAccessToken()).toBeNull();
    expect(((await secureTokenStore.getRefreshToken?.()) ?? null) || null).toBeNull();
    const deleted = secureStore.deleteItemAsync.mock.calls.map(([k]) => k);
    expect(deleted.some((k) => /access/i.test(k))).toBe(true);
    expect(deleted.some((k) => /refresh/i.test(k))).toBe(true);
  });

  it('a failing write does not throw and memory keeps the new tokens', async () => {
    const { secureStore, secureTokenStore } = load();
    secureStore.__control.failWrite = true;
    await expect(
      Promise.resolve(
        secureTokenStore.setTokens({ accessToken: ACCESS_1, refreshToken: REFRESH_1 }),
      ),
    ).resolves.toBeUndefined();
    expect(await secureTokenStore.getAccessToken()).toBe(ACCESS_1);
    expect(await secureTokenStore.getRefreshToken?.()).toBe(REFRESH_1);
  });

  it('a failing delete does not throw and memory is still cleared', async () => {
    const { secureStore, secureTokenStore } = load();
    await secureTokenStore.setTokens({ accessToken: ACCESS_1, refreshToken: REFRESH_1 });
    secureStore.__control.failDelete = true;
    await expect(Promise.resolve(secureTokenStore.clear())).resolves.toBeUndefined();
    expect(await secureTokenStore.getAccessToken()).toBeNull();
  });
});

describe('the session store', () => {
  it('signIn puts the tokens in memory, writes them to the secure store and goes signed-in', async () => {
    const { secureStore, useSessionStore } = load();
    await useSessionStore.getState().signIn({ accessToken: ACCESS_1, refreshToken: REFRESH_1 });
    const state = useSessionStore.getState();
    expect(state.status).toBe('signed-in');
    expect(state.accessToken).toBe(ACCESS_1);
    expect(state.refreshToken).toBe(REFRESH_1);
    expect(secureStore.setItemAsync.mock.calls.map(([, v]) => v).sort()).toEqual(
      [ACCESS_1, REFRESH_1].sort(),
    );
  });

  it('signIn still signs in when the secure-store write fails', async () => {
    const { secureStore, useSessionStore } = load();
    secureStore.__control.failWrite = true;
    await expect(
      Promise.resolve(
        useSessionStore.getState().signIn({ accessToken: ACCESS_1, refreshToken: REFRESH_1 }),
      ),
    ).resolves.not.toThrow();
    await flush();
    expect(useSessionStore.getState().status).toBe('signed-in');
    expect(useSessionStore.getState().accessToken).toBe(ACCESS_1);
  });

  it('signOut nulls both tokens, deletes both keys and goes signed-out', async () => {
    const { secureStore, useSessionStore } = load();
    await useSessionStore.getState().signIn({ accessToken: ACCESS_1, refreshToken: REFRESH_1 });
    await useSessionStore.getState().signOut();
    const state = useSessionStore.getState();
    expect(state.status).toBe('signed-out');
    expect(state.accessToken).toBeNull();
    expect(state.refreshToken).toBeNull();
    const deleted = secureStore.deleteItemAsync.mock.calls.map(([k]) => k);
    expect(deleted.some((k) => /access/i.test(k))).toBe(true);
    expect(deleted.some((k) => /refresh/i.test(k))).toBe(true);
  });

  it('signOut is idempotent and survives a failing delete', async () => {
    const { secureStore, useSessionStore } = load();
    secureStore.__control.failDelete = true;
    await useSessionStore.getState().signOut();
    await useSessionStore.getState().signOut();
    await flush();
    expect(useSessionStore.getState().status).toBe('signed-out');
  });
});

describe('AC1: nothing goes to plain async storage', () => {
  it('has no source file under src/ that imports or requires an async-storage module', () => {
    const offenders = sourceFiles().filter((file) =>
      /(?:from|require\(|import\()\s*['"][^'"]*async-storage/i.test(fs.readFileSync(file, 'utf8')),
    );
    expect(offenders).toEqual([]);
  });

  it('does not list an async-storage package as a dependency of the mobile app', () => {
    const pkg = JSON.parse(
      fs.readFileSync(path.resolve(__dirname, '../../package.json'), 'utf8'),
    ) as { dependencies?: Record<string, string>; devDependencies?: Record<string, string> };
    const names = Object.keys({ ...pkg.dependencies, ...pkg.devDependencies });
    expect(names.filter((n) => /async-storage/.test(n))).toEqual([]);
    expect(names).toContain('expo-secure-store');
  });
});
