// S-2.1.12 (mobile): a failed "Sign out everywhere" keeps the user signed in with an error and a
// "this device only" fallback; the 4 s bound covers the whole call; the expired-session flow runs
// through the real client wiring (src/api.ts + secureTokenStore).
import type { AlertButton } from 'react-native';
import {
  ACCESS_2,
  LOGIN,
  REFRESH,
  REFRESH_1,
  REFRESH_2,
  deferred,
  failure,
  json,
  mountApp,
  sessionStore,
  stubApi,
  tokens,
  unmountApp,
} from './app-harness';

jest.mock('expo-secure-store', () =>
  jest.requireActual<typeof import('./app-harness')>('./app-harness').createSecureStoreMock(),
);
jest.setTimeout(60000);

const LOGOUT = '/api/v1/auth/logout';
const LOGOUT_ALL = '/api/v1/auth/logout-all';
const ME = '/api/v1/auth/me';
const BOUND_MS = 4000;

const unhandled: unknown[] = [];
const onUnhandled = (reason: unknown) => {
  unhandled.push(reason);
};

// jest.useRealTimers() when timers were never faked leaves the worker with an open handle.
let fakeTimers = false;
function useFakeTimers() {
  fakeTimers = true;
  jest.useFakeTimers();
}

beforeEach(() => {
  unhandled.length = 0;
  process.on('unhandledRejection', onUnhandled);
});

afterEach(async () => {
  process.off('unhandledRejection', onUnhandled);
  await unmountApp();
  if (fakeTimers) {
    jest.clearAllTimers();
  }
  fakeTimers = false;
  jest.restoreAllMocks();
});

type App = Awaited<ReturnType<typeof mountApp>>;

function launchSignedIn(url = '/play') {
  return mountApp(url, (store) => {
    store.__control.seeded.refresh = REFRESH_1;
  });
}

async function openMore(app: App) {
  await app.screen.findByLabelText('More');
  await app.fireEvent.press(app.screen.getByLabelText('More'));
  return app.screen.findByTestId('sign-out');
}

function deletedKeys(app: App) {
  return app.secureStore.deleteItemAsync.mock.calls.map(([k]) => k);
}

function expectBothTokensDeleted(app: App) {
  expect(deletedKeys(app).some((k) => /access/i.test(k))).toBe(true);
  expect(deletedKeys(app).some((k) => /refresh/i.test(k))).toBe(true);
}

/** Opens More, presses "Sign out everywhere" and confirms the Alert. */
async function confirmEverywhere(app: App) {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { Alert } = require('react-native') as typeof import('react-native');
  const alert = jest.spyOn(Alert, 'alert').mockImplementation(() => undefined);
  await openMore(app);
  await app.fireEvent.press(app.screen.getByTestId('sign-out-everywhere'));
  const buttons = alert.mock.calls[0]![2] as AlertButton[];
  const confirm = buttons.find((b) => b.text === 'Sign out everywhere')!;
  await app.lib.act(async () => confirm.onPress?.());
}

async function advance(app: App, ms: number) {
  await app.lib.act(async () => {
    await jest.advanceTimersByTimeAsync(ms);
  });
}

const NEVER = () => new Promise<Response>(() => undefined);

const FAILURES: Array<[string, () => Response | Promise<Response>]> = [
  ['HTTP 500', () => failure(500, 'internal_error', 'Boom.')],
  [
    'a network error',
    () => {
      throw new TypeError('Network request failed');
    },
  ],
];

describe.each(FAILURES)('Sign out everywhere failing with %s', (_name, handler) => {
  async function failedApp() {
    const api = stubApi({
      [REFRESH]: () => tokens(ACCESS_2, REFRESH_2),
      [LOGOUT_ALL]: handler,
      [LOGOUT]: () => new Response(null, { status: 204 }),
    });
    const app = await launchSignedIn();
    await confirmEverywhere(app);
    await app.screen.findByTestId('sign-out-error');
    return { app, api };
  }

  it('stays signed in and says so, with other devices possibly not signed out', async () => {
    const { app } = await failedApp();
    const text = String(app.screen.getByTestId('sign-out-error').props.children);
    expect(text).toMatch(/still signed in on this device/i);
    expect(text).toMatch(/other devices may not have been signed out/i);
    expect(sessionStore().getState().status).toBe('signed-in');
    expect(sessionStore().getState().refreshToken).toBe(REFRESH_2);
    expect(deletedKeys(app)).toHaveLength(0);
    expect(app.screen.getByTestId('sign-out')).toBeTruthy();
  });

  it('shows the "Sign out on this device only" button with the button role and label', async () => {
    const { app } = await failedApp();
    expect(app.screen.getByTestId('sign-out-this-device')).toBeTruthy();
    expect(app.screen.getByRole('button', { name: 'Sign out on this device only' })).toBeTruthy();
    expect(app.screen.getByLabelText('Sign out on this device only')).toBeTruthy();
  });

  it('this-device-only clears the error and signs out locally (tokens, cache, sign-in screen)', async () => {
    const { app, api } = await failedApp();
    await app.fireEvent.press(app.screen.getByTestId('sign-out-this-device'));
    expect(await app.screen.findByLabelText(/username/i)).toBeTruthy();
    expect(app.screen).toHavePathname('/sign-in');
    expect(app.screen.queryByTestId('sign-out-error')).toBeNull();
    expect(api.to(LOGOUT)).toHaveLength(1);
    expect(api.to(LOGOUT)[0]!.body).toEqual({ refresh_token: REFRESH_2 });
    expectBothTokensDeleted(app);
    expect(sessionStore().getState().status).toBe('signed-out');
    expect(sessionStore().getState().refreshToken).toBeNull();
  });

  it('there is no error and no this-device button before anything failed', async () => {
    stubApi({ [REFRESH]: () => tokens(ACCESS_2, REFRESH_2) });
    const app = await launchSignedIn();
    await openMore(app);
    expect(app.screen.queryByTestId('sign-out-error')).toBeNull();
    expect(app.screen.queryByTestId('sign-out-this-device')).toBeNull();
  });
});

describe('this-device-only fallback', () => {
  it('signs out locally even when /auth/logout also fails', async () => {
    stubApi({
      [REFRESH]: () => tokens(ACCESS_2, REFRESH_2),
      [LOGOUT_ALL]: () => failure(500, 'internal_error', 'Boom.'),
      [LOGOUT]: () => {
        throw new TypeError('Network request failed');
      },
    });
    const app = await launchSignedIn();
    await confirmEverywhere(app);
    await app.screen.findByTestId('sign-out-error');
    await app.fireEvent.press(app.screen.getByTestId('sign-out-this-device'));
    expect(await app.screen.findByLabelText(/username/i)).toBeTruthy();
    expectBothTokensDeleted(app);
    expect(sessionStore().getState().status).toBe('signed-out');
  });

  it('is disabled while the plain sign-out is pending, with the error still shown', async () => {
    const hold = deferred();
    stubApi({
      [REFRESH]: () => tokens(ACCESS_2, REFRESH_2),
      [LOGOUT_ALL]: () => failure(500, 'internal_error', 'Boom.'),
      [LOGOUT]: () => hold.promise,
    });
    const app = await launchSignedIn();
    await confirmEverywhere(app);
    await app.screen.findByTestId('sign-out-error');
    expect(app.screen.getByTestId('sign-out-this-device')).toBeEnabled();
    await app.fireEvent.press(app.screen.getByTestId('sign-out'));
    await app.screen.findByText('Signing out…');
    expect(app.screen.getByTestId('sign-out-this-device')).toBeDisabled();
    expect(app.screen.getByTestId('sign-out-everywhere')).toBeDisabled();
    await app.lib.act(async () => hold.release(new Response(null, { status: 204 })));
    expect(await app.screen.findByLabelText(/username/i)).toBeTruthy();
  });

  it('pressing it clears the error at once and disables the other sign-out buttons while pending', async () => {
    const hold = deferred();
    const api = stubApi({
      [REFRESH]: () => tokens(ACCESS_2, REFRESH_2),
      [LOGOUT_ALL]: () => failure(500, 'internal_error', 'Boom.'),
      [LOGOUT]: () => hold.promise,
    });
    const app = await launchSignedIn();
    await confirmEverywhere(app);
    await app.screen.findByTestId('sign-out-error');
    await app.fireEvent.press(app.screen.getByTestId('sign-out-this-device'));
    await app.screen.findByText('Signing out…');
    expect(app.screen.queryByTestId('sign-out-error')).toBeNull();
    expect(app.screen.getByTestId('sign-out')).toBeDisabled();
    expect(app.screen.getByTestId('sign-out-everywhere')).toBeDisabled();
    await app.fireEvent.press(app.screen.getByTestId('sign-out'));
    expect(api.to(LOGOUT)).toHaveLength(1);
    await app.lib.act(async () => hold.release(new Response(null, { status: 204 })));
    expect(await app.screen.findByLabelText(/username/i)).toBeTruthy();
  });

  it('is disabled while "Sign out everywhere" is pending (the error is cleared on retry)', async () => {
    const hold = deferred();
    let attempt = 0;
    stubApi({
      [REFRESH]: () => tokens(ACCESS_2, REFRESH_2),
      [LOGOUT_ALL]: () => {
        attempt += 1;
        return attempt === 1 ? failure(500, 'internal_error', 'Boom.') : hold.promise;
      },
    });
    const app = await launchSignedIn();
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const { Alert } = require('react-native') as typeof import('react-native');
    const alert = jest.spyOn(Alert, 'alert').mockImplementation(() => undefined);
    await openMore(app);
    const press = async () => {
      await app.fireEvent.press(app.screen.getByTestId('sign-out-everywhere'));
      const buttons = alert.mock.calls.at(-1)![2] as AlertButton[];
      await app.lib.act(async () =>
        buttons.find((b) => b.text === 'Sign out everywhere')!.onPress?.(),
      );
    };
    await press();
    await app.screen.findByTestId('sign-out-error');
    await press();
    await app.lib.waitFor(() => expect(app.screen.queryByTestId('sign-out-error')).toBeNull());
    expect(sessionStore().getState().status).toBe('signed-in');
    await app.lib.act(async () => hold.release(new Response(null, { status: 204 })));
    expect(await app.screen.findByLabelText(/username/i)).toBeTruthy();
  });
});

describe('the 4 s bound covers the whole call', () => {
  it('a hung /auth/logout-all shows the error path within the bound and re-enables the buttons', async () => {
    const api = stubApi({ [REFRESH]: () => tokens(ACCESS_2, REFRESH_2), [LOGOUT_ALL]: NEVER });
    const app = await launchSignedIn();
    await openMore(app);
    useFakeTimers();
    await confirmEverywhere(app);
    await advance(app, BOUND_MS - 500);
    expect(app.screen.getByTestId('sign-out')).toBeDisabled();
    expect(app.screen.getByTestId('sign-out-everywhere')).toBeDisabled();
    expect(app.screen.queryByTestId('sign-out-error')).toBeNull();
    await advance(app, 600);
    expect(app.screen.getByTestId('sign-out-error')).toBeTruthy();
    expect(app.screen.getByTestId('sign-out')).toBeEnabled();
    expect(app.screen.getByTestId('sign-out-everywhere')).toBeEnabled();
    expect(app.screen.getByTestId('sign-out-this-device')).toBeEnabled();
    expect(sessionStore().getState().status).toBe('signed-in');
    expect(api.to(LOGOUT_ALL)).toHaveLength(1);
    expect(deletedKeys(app)).toHaveLength(0);
  });

  it('a hung token refresh before /auth/logout-all does not keep the buttons disabled past the bound', async () => {
    const refresh = deferred();
    let refreshCalls = 0;
    stubApi({
      // First call is the launch restore; later ones are the middleware refresh, which never answers.
      [REFRESH]: () => {
        refreshCalls += 1;
        return refreshCalls === 1 ? tokens(ACCESS_2, REFRESH_2) : refresh.promise;
      },
      [LOGOUT_ALL]: () => failure(401, 'invalid_token', 'Expired.'),
    });
    const app = await launchSignedIn();
    await openMore(app);
    useFakeTimers();
    await confirmEverywhere(app);
    await advance(app, BOUND_MS - 500);
    expect(app.screen.getByTestId('sign-out-everywhere')).toBeDisabled();
    expect(refreshCalls).toBe(2);
    await advance(app, 600);
    expect(app.screen.getByTestId('sign-out-error')).toBeTruthy();
    expect(app.screen.getByTestId('sign-out')).toBeEnabled();
    expect(app.screen.getByTestId('sign-out-everywhere')).toBeEnabled();
    expect(sessionStore().getState().status).toBe('signed-in');
  });

  it('a refresh resolving after the user signed out leaves them signed out with no tokens stored', async () => {
    const refresh = deferred();
    let refreshCalls = 0;
    stubApi({
      [REFRESH]: () => {
        refreshCalls += 1;
        return refreshCalls === 1 ? tokens(ACCESS_2, REFRESH_2) : refresh.promise;
      },
      [LOGOUT_ALL]: () => failure(401, 'invalid_token', 'Expired.'),
    });
    const app = await launchSignedIn();
    await openMore(app);
    useFakeTimers();
    await confirmEverywhere(app);
    await advance(app, BOUND_MS + 100);
    expect(app.screen.getByTestId('sign-out-error')).toBeTruthy();
    await app.fireEvent.press(app.screen.getByTestId('sign-out-this-device'));
    expect(await app.screen.findByLabelText(/username/i)).toBeTruthy();
    // The abandoned refresh now succeeds, late.
    await app.lib.act(async () => refresh.release(tokens('late-access', 'late-refresh')));
    await advance(app, 50);
    expect(sessionStore().getState().status).toBe('signed-out');
    expect(sessionStore().getState().accessToken).toBeNull();
    expect(sessionStore().getState().refreshToken).toBeNull();
    const storedKeys = app.secureStore.setItemAsync.mock.calls
      .filter(([, v]) => v === 'late-refresh' || v === 'late-access')
      .map(([k]) => k);
    expect(storedKeys).toEqual([]);
    expect(app.secureStore.__control.seeded.refresh).toBeNull();
    expect(app.screen.getByLabelText(/username/i)).toBeTruthy();
    expect(unhandled).toEqual([]);
  });

  it('a hung plain /auth/logout still signs out locally within the bound', async () => {
    stubApi({ [REFRESH]: () => tokens(ACCESS_2, REFRESH_2), [LOGOUT]: NEVER });
    const app = await launchSignedIn();
    const signOutButton = await openMore(app);
    useFakeTimers();
    await app.fireEvent.press(signOutButton);
    await advance(app, BOUND_MS - 500);
    expect(sessionStore().getState().status).toBe('signed-in');
    await advance(app, 600);
    expect(await app.screen.findByLabelText(/username/i)).toBeTruthy();
    expectBothTokensDeleted(app);
    expect(sessionStore().getState().status).toBe('signed-out');
  });

  it('a hung /auth/logout that settles late causes no unhandled rejection', async () => {
    const hold = deferred();
    stubApi({ [REFRESH]: () => tokens(ACCESS_2, REFRESH_2), [LOGOUT]: () => hold.promise });
    const app = await launchSignedIn();
    const signOutButton = await openMore(app);
    useFakeTimers();
    await app.fireEvent.press(signOutButton);
    await advance(app, BOUND_MS + 100);
    expect(await app.screen.findByLabelText(/username/i)).toBeTruthy();
    await app.lib.act(async () => hold.release(failure(500, 'internal_error', 'Late.')));
    await advance(app, 50);
    expect(sessionStore().getState().status).toBe('signed-out');
    expect(unhandled).toEqual([]);
  });

  it('a hung /auth/logout-all that rejects late causes no unhandled rejection and no sign-in', async () => {
    const hold = deferred();
    stubApi({ [REFRESH]: () => tokens(ACCESS_2, REFRESH_2), [LOGOUT_ALL]: () => hold.promise });
    const app = await launchSignedIn();
    await openMore(app);
    useFakeTimers();
    await confirmEverywhere(app);
    await advance(app, BOUND_MS + 100);
    expect(app.screen.getByTestId('sign-out-error')).toBeTruthy();
    await app.lib.act(async () => hold.release(failure(500, 'internal_error', 'Late.')));
    await advance(app, 50);
    expect(unhandled).toEqual([]);
    expect(sessionStore().getState().status).toBe('signed-in');
  });
});

describe('Session expiry through the real client wiring', () => {
  async function signInAgain(app: App) {
    await app.fireEvent.changeText(app.screen.getByLabelText(/username/i), 'ada');
    await app.fireEvent.changeText(app.screen.getByLabelText(/password/i), 'correct horse battery');
    await app.fireEvent.press(app.screen.getByRole('button', { name: 'Sign in' }));
  }

  it('a protected 401 then a rejected refresh lands on sign-in with the message, and sign-in returns to the screen', async () => {
    let refreshCalls = 0;
    const api = stubApi({
      [REFRESH]: () => {
        refreshCalls += 1;
        return refreshCalls === 1
          ? tokens(ACCESS_2, REFRESH_2)
          : failure(401, 'invalid_refresh', 'Session expired.');
      },
      [ME]: () => failure(401, 'invalid_token', 'Expired.'),
      [LOGIN]: () => tokens(),
    });
    const app = await launchSignedIn('/decks');
    await app.screen.findByRole('header', { name: 'Decks' });
    expect(sessionStore().getState().status).toBe('signed-in');

    let failed: unknown = null;
    await app.lib.act(async () => {
      await app.api!.GET('/api/v1/auth/me').catch((e: unknown) => {
        failed = e;
      });
    });
    expect(failed).toMatchObject({ message: 'Expired.' });

    expect(await app.screen.findByTestId('sign-in-session-expired')).toHaveTextContent(
      'Your session has expired',
    );
    expect(app.screen).toHavePathname('/sign-in');
    expect(refreshCalls).toBe(2);
    expect(api.to(ME).length).toBeGreaterThanOrEqual(1);
    expect(sessionStore().getState().status).toBe('signed-out');
    expect(sessionStore().getState().refreshToken).toBeNull();
    expectBothTokensDeleted(app);
    expect(sessionStore().getState().pendingHref).toBe('/decks');

    await signInAgain(app);
    expect(await app.screen.findByRole('header', { name: 'Decks' })).toBeTruthy();
    expect(app.screen).toHavePathname('/decks');
    expect(app.screen.queryByTestId('sign-in-session-expired')).toBeNull();
    expect(sessionStore().getState().status).toBe('signed-in');
  });

  it('a 401 that the refresh fixes does not sign the user out', async () => {
    let meCalls = 0;
    stubApi({
      [REFRESH]: () => tokens(ACCESS_2, REFRESH_2),
      [ME]: () => {
        meCalls += 1;
        return meCalls === 1 ? failure(401, 'invalid_token', 'Expired.') : json({ id: 'u1' });
      },
    });
    const app = await launchSignedIn('/decks');
    await app.screen.findByRole('header', { name: 'Decks' });
    let result: unknown = null;
    await app.lib.act(async () => {
      result = await app.api!.GET('/api/v1/auth/me');
    });
    expect(result).toMatchObject({ id: 'u1' });
    expect(meCalls).toBe(2);
    expect(sessionStore().getState().status).toBe('signed-in');
    expect(app.screen.queryByTestId('sign-in-session-expired')).toBeNull();
  });
});
