// S-2.1.7 AC6 and decision 4: restore on launch from the refresh token in expo-secure-store.
import {
  ACCESS_2,
  REFRESH,
  REFRESH_1,
  REFRESH_2,
  failure,
  mountApp,
  stubApi,
  tokens,
  unmountApp,
} from './app-harness';

jest.mock('expo-secure-store', () =>
  jest.requireActual<typeof import('./app-harness')>('./app-harness').createSecureStoreMock(),
);
jest.setTimeout(60000);

afterEach(async () => {
  await unmountApp();
  jest.restoreAllMocks();
});

/** Mounts the app after seeding the secure store, like a relaunch of an installed app. */
function launch(url: string, seed: { refresh?: string; access?: string; failRead?: boolean } = {}) {
  return mountApp(url, (store) => {
    store.__control.seeded.refresh = seed.refresh ?? null;
    store.__control.seeded.access = seed.access ?? null;
    store.__control.failRead = seed.failRead ?? false;
  });
}

describe('restore with nothing stored', () => {
  it('goes signed out with no network call and shows the sign-in screen', async () => {
    const api = stubApi();
    const app = await launch('/');
    expect(await app.screen.findByLabelText(/username/i)).toBeTruthy();
    expect(app.screen).toHavePathname('/sign-in');
    expect(api.calls).toHaveLength(0);
    expect(app.screen.queryByLabelText('Play')).toBeNull();
  });
});

describe('restore with a stored refresh token', () => {
  it('refreshes in body mode, rotates the stored tokens and lands on /play', async () => {
    const api = stubApi({ [REFRESH]: () => tokens(ACCESS_2, REFRESH_2) });
    const app = await launch('/', { refresh: REFRESH_1 });
    expect(await app.screen.findByLabelText('Play')).toBeTruthy();
    expect(app.screen).toHavePathname('/play');
    expect(api.to(REFRESH)).toHaveLength(1);
    expect(api.to(REFRESH)[0]!.body).toEqual({ refresh_token: REFRESH_1 });
    expect(api.to(REFRESH)[0]!.headers.get('X-Client')).toBe('mobile');
    const writes = app.secureStore.setItemAsync.mock.calls;
    expect(writes.find(([k, v]) => v === ACCESS_2 && /access/i.test(k))).toBeDefined();
    expect(writes.find(([k, v]) => v === REFRESH_2 && /refresh/i.test(k))).toBeDefined();
  });

  it('shows neither the sign-in form nor the tabs while the refresh is in flight', async () => {
    let release: (r: Response) => void = () => undefined;
    stubApi({
      [REFRESH]: () =>
        new Promise<Response>((resolve) => {
          release = resolve;
        }),
    });
    const app = await launch('/', { refresh: REFRESH_1 });
    expect(app.screen.queryByLabelText(/username/i)).toBeNull();
    expect(app.screen.queryByLabelText('Play')).toBeNull();
    await app.lib.act(async () => release(tokens(ACCESS_2, REFRESH_2)));
    expect(await app.screen.findByLabelText('Play')).toBeTruthy();
  });

  it('on a rejected refresh clears the secure store and goes signed out', async () => {
    stubApi({ [REFRESH]: () => failure(401, 'invalid_refresh', 'Session expired.') });
    const app = await launch('/', { refresh: REFRESH_1 });
    expect(await app.screen.findByLabelText(/username/i)).toBeTruthy();
    expect(app.screen).toHavePathname('/sign-in');
    const deleted = app.secureStore.deleteItemAsync.mock.calls.map(([k]) => k);
    expect(deleted.some((k) => /access/i.test(k))).toBe(true);
    expect(deleted.some((k) => /refresh/i.test(k))).toBe(true);
    expect(await app.secureStore.getItemAsync('refresh')).toBeNull();
  });

  it('on a 5xx keeps the tokens and stays signed in', async () => {
    stubApi({ [REFRESH]: () => failure(503, 'unavailable', 'Try later.') });
    const app = await launch('/', { refresh: REFRESH_1 });
    expect(await app.screen.findByLabelText('Play')).toBeTruthy();
    expect(app.screen).toHavePathname('/play');
    expect(app.secureStore.deleteItemAsync).not.toHaveBeenCalled();
  });

  it('when offline keeps the tokens and stays signed in', async () => {
    stubApi({
      [REFRESH]: () => {
        throw new TypeError('Network request failed');
      },
    });
    const app = await launch('/', { refresh: REFRESH_1 });
    expect(await app.screen.findByLabelText('Play')).toBeTruthy();
    expect(app.secureStore.deleteItemAsync).not.toHaveBeenCalled();
  });

  it('opens a protected deep link directly, with no detour through sign-in', async () => {
    stubApi({ [REFRESH]: () => tokens(ACCESS_2, REFRESH_2) });
    const app = await launch('/join/ABC123', { refresh: REFRESH_1 });
    expect(await app.screen.findByTestId('join-screen')).toBeTruthy();
    expect(app.screen).toHavePathname('/join/ABC123');
  });
});

describe('restore when expo-secure-store fails', () => {
  it('treats a read error as no stored token: signed out, no network call', async () => {
    const api = stubApi();
    const app = await launch('/', { refresh: REFRESH_1, failRead: true });
    expect(await app.screen.findByLabelText(/username/i)).toBeTruthy();
    expect(app.screen).toHavePathname('/sign-in');
    expect(api.calls).toHaveLength(0);
  });
});
