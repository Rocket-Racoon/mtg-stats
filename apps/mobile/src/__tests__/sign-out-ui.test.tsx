// S-2.1.12 (mobile): sign out and sign out everywhere from the More tab, and the session-expired flow.
import type { AlertButton } from 'react-native';
import {
  ACCESS_2,
  LOGIN,
  REFRESH,
  REFRESH_1,
  REFRESH_2,
  failure,
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

afterEach(async () => {
  await unmountApp();
  jest.restoreAllMocks();
});

type App = Awaited<ReturnType<typeof mountApp>>;

/** Launches with a stored refresh token (restore succeeds), optionally on a given URL. */
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

/** Spies the Alert of the freshly loaded module registry (mountApp resets modules per launch). */
function spyAlert() {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { Alert } = require('react-native') as typeof import('react-native');
  return jest.spyOn(Alert, 'alert').mockImplementation(() => undefined);
}
function alertButtons(spy: jest.SpyInstance): AlertButton[] {
  return spy.mock.calls[0]![2] as AlertButton[];
}

describe('Sign out button', () => {
  it('has the button role and the label "Sign out", and the everywhere button likewise', async () => {
    stubApi({ [REFRESH]: () => tokens(ACCESS_2, REFRESH_2) });
    const app = await launchSignedIn();
    await openMore(app);
    expect(app.screen.getByRole('button', { name: 'Sign out' })).toBeTruthy();
    expect(app.screen.getByRole('button', { name: 'Sign out everywhere' })).toBeTruthy();
    expect(app.screen.getByTestId('sign-out-everywhere')).toBeTruthy();
  });

  it('calls POST /auth/logout with the refresh token, deletes both tokens and shows sign-in', async () => {
    const api = stubApi({
      [REFRESH]: () => tokens(ACCESS_2, REFRESH_2),
      [LOGOUT]: () => new Response(null, { status: 204 }),
    });
    const app = await launchSignedIn();
    await app.fireEvent.press(await openMore(app));
    expect(await app.screen.findByLabelText(/username/i)).toBeTruthy();
    expect(app.screen).toHavePathname('/sign-in');
    expect(api.to(LOGOUT)).toHaveLength(1);
    expect(api.to(LOGOUT)[0]!.method).toBe('POST');
    expect(api.to(LOGOUT)[0]!.body).toEqual({ refresh_token: REFRESH_2 });
    expect(api.to(LOGOUT_ALL)).toHaveLength(0);
    expectBothTokensDeleted(app);
    expect(sessionStore().getState().status).toBe('signed-out');
    expect(sessionStore().getState().accessToken).toBeNull();
    expect(sessionStore().getState().refreshToken).toBeNull();
    expect(app.screen.queryByTestId('sign-in-session-expired')).toBeNull();
  });

  it('still signs out locally when the network is down', async () => {
    const api = stubApi({
      [REFRESH]: () => tokens(ACCESS_2, REFRESH_2),
      [LOGOUT]: () => {
        throw new TypeError('Network request failed');
      },
    });
    const app = await launchSignedIn();
    await app.fireEvent.press(await openMore(app));
    expect(await app.screen.findByLabelText(/username/i)).toBeTruthy();
    expect(api.to(LOGOUT)).toHaveLength(1);
    expectBothTokensDeleted(app);
    expect(sessionStore().getState().status).toBe('signed-out');
  });

  it('still signs out locally when /auth/logout answers 500', async () => {
    stubApi({
      [REFRESH]: () => tokens(ACCESS_2, REFRESH_2),
      [LOGOUT]: () => failure(500, 'internal_error', 'Boom.'),
    });
    const app = await launchSignedIn();
    await app.fireEvent.press(await openMore(app));
    expect(await app.screen.findByLabelText(/username/i)).toBeTruthy();
    expectBothTokensDeleted(app);
    expect(sessionStore().getState().status).toBe('signed-out');
  });
});

describe('Sign out everywhere', () => {
  it('asks for confirmation first and makes no API call until confirmed', async () => {
    const api = stubApi({ [REFRESH]: () => tokens(ACCESS_2, REFRESH_2) });
    const app = await launchSignedIn();
    const alert = spyAlert();
    await openMore(app);
    await app.fireEvent.press(app.screen.getByTestId('sign-out-everywhere'));
    expect(alert).toHaveBeenCalledTimes(1);
    const labels = alertButtons(alert).map((b) => b.text);
    expect(labels).toContain('Cancel');
    expect(labels).toContain('Sign out everywhere');
    expect(api.to(LOGOUT_ALL)).toHaveLength(0);
    expect(sessionStore().getState().status).toBe('signed-in');
  });

  it('does nothing when Cancel is chosen', async () => {
    const api = stubApi({ [REFRESH]: () => tokens(ACCESS_2, REFRESH_2) });
    const app = await launchSignedIn();
    const alert = spyAlert();
    await openMore(app);
    await app.fireEvent.press(app.screen.getByTestId('sign-out-everywhere'));
    const cancel = alertButtons(alert).find((b) => b.text === 'Cancel')!;
    await app.lib.act(async () => cancel.onPress?.());
    expect(api.to(LOGOUT_ALL)).toHaveLength(0);
    expect(api.to(LOGOUT)).toHaveLength(0);
    expect(sessionStore().getState().status).toBe('signed-in');
    expect(app.screen.getByTestId('sign-out')).toBeTruthy();
    expect(deletedKeys(app)).toHaveLength(0);
  });

  it('calls POST /auth/logout-all when confirmed, then signs out and shows sign-in', async () => {
    const api = stubApi({
      [REFRESH]: () => tokens(ACCESS_2, REFRESH_2),
      [LOGOUT_ALL]: () => new Response(null, { status: 204 }),
    });
    const app = await launchSignedIn();
    const alert = spyAlert();
    await openMore(app);
    await app.fireEvent.press(app.screen.getByTestId('sign-out-everywhere'));
    const confirm = alertButtons(alert).find((b) => b.text === 'Sign out everywhere')!;
    await app.lib.act(async () => confirm.onPress?.());
    expect(await app.screen.findByLabelText(/username/i)).toBeTruthy();
    expect(api.to(LOGOUT_ALL)).toHaveLength(1);
    expect(api.to(LOGOUT_ALL)[0]!.method).toBe('POST');
    expectBothTokensDeleted(app);
    expect(sessionStore().getState().status).toBe('signed-out');
  });

  // The implementation chose this (the criteria do not say): a failed logout-all keeps the user
  // signed in and shows an error, so they are not told every device was signed out when it was not.
  it('stays signed in and shows sign-out-error when /auth/logout-all fails', async () => {
    stubApi({
      [REFRESH]: () => tokens(ACCESS_2, REFRESH_2),
      [LOGOUT_ALL]: () => failure(500, 'internal_error', 'Boom.'),
    });
    const app = await launchSignedIn();
    const alert = spyAlert();
    await openMore(app);
    await app.fireEvent.press(app.screen.getByTestId('sign-out-everywhere'));
    const confirm = alertButtons(alert).find((b) => b.text === 'Sign out everywhere')!;
    await app.lib.act(async () => confirm.onPress?.());
    expect(await app.screen.findByTestId('sign-out-error')).toBeTruthy();
    expect(sessionStore().getState().status).toBe('signed-in');
    expect(app.screen.getByTestId('sign-out')).toBeTruthy();
    expect(deletedKeys(app)).toHaveLength(0);
  });
});

describe('Session expired on a protected screen', () => {
  async function expire(app: App) {
    const { onSessionExpired } = jest.requireActual<typeof import('../session/secureTokenStore')>(
      '../session/secureTokenStore',
    );
    await app.lib.act(async () => onSessionExpired());
  }

  async function signInAgain(app: App) {
    await app.fireEvent.changeText(app.screen.getByLabelText(/username/i), 'ada');
    await app.fireEvent.changeText(app.screen.getByLabelText(/password/i), 'correct horse battery');
    await app.fireEvent.press(app.screen.getByRole('button', { name: 'Sign in' }));
  }

  it('shows "Your session has expired" on sign-in and returns to the same screen after sign-in', async () => {
    stubApi({ [REFRESH]: () => tokens(ACCESS_2, REFRESH_2), [LOGIN]: () => tokens() });
    const app = await launchSignedIn('/decks');
    await app.screen.findByRole('header', { name: 'Decks' });
    await expire(app);
    expect(await app.screen.findByTestId('sign-in-session-expired')).toHaveTextContent(
      'Your session has expired',
    );
    expect(app.screen).toHavePathname('/sign-in');
    expect(sessionStore().getState().pendingHref).toBe('/decks');
    await signInAgain(app);
    expect(await app.screen.findByRole('header', { name: 'Decks' })).toBeTruthy();
    expect(app.screen).toHavePathname('/decks');
    expect(sessionStore().getState().pendingHref).toBeNull();
    expect(app.screen.queryByTestId('sign-in-session-expired')).toBeNull();
  });

  it('does not keep /play as the pending link', async () => {
    stubApi({ [REFRESH]: () => tokens(ACCESS_2, REFRESH_2) });
    const app = await launchSignedIn('/play');
    await app.screen.findByLabelText('Play');
    await expire(app);
    expect(await app.screen.findByTestId('sign-in-session-expired')).toBeTruthy();
    expect(sessionStore().getState().pendingHref).toBeNull();
  });

  it('round-trips an encoded path segment through sign-in', async () => {
    stubApi({ [REFRESH]: () => tokens(ACCESS_2, REFRESH_2), [LOGIN]: () => tokens() });
    const app = await launchSignedIn('/decks');
    await app.screen.findByRole('header', { name: 'Decks' });
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const { router } = require('expo-router') as typeof import('expo-router');
    await app.lib.act(async () => router.push('/join/AB%20C%2F1'));
    await app.screen.findByTestId('join-screen-value');
    await expire(app);
    await app.screen.findByTestId('sign-in-session-expired');
    await signInAgain(app);
    expect(await app.screen.findByTestId('join-screen-value')).toHaveTextContent('AB C/1');
  });

  it('shows no expired message when the refresh is rejected during the launch restore', async () => {
    stubApi({ [REFRESH]: () => failure(401, 'invalid_refresh', 'Session expired.') });
    const app = await launchSignedIn('/decks');
    expect(await app.screen.findByLabelText(/username/i)).toBeTruthy();
    expect(app.screen.queryByTestId('sign-in-session-expired')).toBeNull();
    expect(app.screen.queryByText('Your session has expired')).toBeNull();
  });
});
