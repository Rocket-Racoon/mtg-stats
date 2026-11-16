// S-2.1.7 review m2 and m5: a refresh that finishes after sign-out does not resurrect the session,
// and a pending sign-in cannot be submitted twice.
import {
  ACCESS_1,
  ACCESS_2,
  LOGIN,
  REFRESH,
  REFRESH_1,
  REFRESH_2,
  deferred,
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

const LIVE = '/api/v1/health/live';

afterEach(async () => {
  await unmountApp();
  jest.restoreAllMocks();
});

type App = Awaited<ReturnType<typeof mountApp>>;

async function fillSignIn(app: App) {
  await app.fireEvent.changeText(await app.screen.findByLabelText(/username/i), 'ada');
  await app.fireEvent.changeText(app.screen.getByLabelText(/password/i), 'correct horse battery');
}

describe('m2: a refresh that finishes after sign-out', () => {
  it('does not put the tokens back in memory or in the secure store', async () => {
    const refresh = deferred();
    const api = stubApi({
      [LOGIN]: () => tokens(ACCESS_1, REFRESH_1),
      [REFRESH]: () => refresh.promise,
      [LIVE]: () => failure(401, 'token_expired', 'Expired.'),
    });
    const app = await mountApp('/sign-in');
    await fillSignIn(app);
    await app.fireEvent.press(app.screen.getByRole('button', { name: 'Sign in' }));
    await app.screen.findByLabelText('Play');

    // A request hits a 401 and its refresh is left in flight.
    let request: Promise<unknown> = Promise.resolve();
    await app.lib.act(async () => {
      request = app.api.GET(LIVE).catch(() => undefined);
      await app.lib.waitFor(() => expect(api.to(REFRESH)).toHaveLength(1));
    });

    // The user signs out meanwhile.
    await app.lib.act(async () => {
      await sessionStore().getState().signOut();
    });
    const writesAtSignOut = app.secureStore.setItemAsync.mock.calls.length;

    // The refresh then succeeds with new tokens.
    await app.lib.act(async () => {
      refresh.release(tokens(ACCESS_2, REFRESH_2));
      await request;
    });

    const state = sessionStore().getState();
    expect(state.status).toBe('signed-out');
    expect(state.accessToken).toBeNull();
    expect(state.refreshToken).toBeNull();
    const later = app.secureStore.setItemAsync.mock.calls.slice(writesAtSignOut);
    expect(later).toEqual([]);
    expect(await app.secureStore.getItemAsync('refresh')).toBeNull();
    expect(await app.secureStore.getItemAsync('access')).toBeNull();
  });
});

describe('m5: a sign-in that is still in flight', () => {
  it('disables submit and sends nothing on a second press', async () => {
    const login = deferred();
    const api = stubApi({ [LOGIN]: () => login.promise });
    const app = await mountApp('/sign-in');
    await fillSignIn(app);
    const submit = () => app.screen.getByTestId('sign-in-submit');
    await app.fireEvent.press(submit());
    await app.lib.waitFor(() => expect(api.to(LOGIN)).toHaveLength(1));
    await app.lib.waitFor(() => expect(submit()).toBeDisabled());
    // The busy label is what users and screen readers get (U+2026, not a replacement glyph).
    expect(app.screen.getByLabelText('Signing in…')).toBeTruthy();
    await app.fireEvent.press(submit());
    await app.fireEvent(app.screen.getByLabelText(/password/i), 'submitEditing');
    expect(api.to(LOGIN)).toHaveLength(1);

    await app.lib.act(async () => login.release(tokens()));
    expect(await app.screen.findByLabelText('Play')).toBeTruthy();
    expect(api.to(LOGIN)).toHaveLength(1);
  });

  it('enables submit again after a failure', async () => {
    const login = deferred();
    stubApi({ [LOGIN]: () => login.promise });
    const app = await mountApp('/sign-in');
    await fillSignIn(app);
    await app.fireEvent.press(app.screen.getByTestId('sign-in-submit'));
    await app.lib.act(async () =>
      login.release(failure(401, 'invalid_credentials', 'Incorrect username or password.')),
    );
    await app.screen.findByText('Incorrect username or password.');
    expect(app.screen.getByTestId('sign-in-submit')).toBeEnabled();
  });
});
