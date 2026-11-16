// S-2.1.7 review M1 and m1: while the launch restore is in flight, the forms cannot submit and the
// screens underneath are hidden from assistive technology. The refresh is held pending, the form
// is driven through the accessibility tree (hidden elements included), and the restored session
// must survive.
import {
  ACCESS_2,
  LOGIN,
  PREVIEW,
  REFRESH,
  REFRESH_1,
  REFRESH_2,
  REGISTER,
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

afterEach(async () => {
  await unmountApp();
  jest.restoreAllMocks();
});

const ALL = { includeHiddenElements: true };
const preview = () =>
  json({ inviter_display_name: 'Ada Lovelace', expires_at: '2030-01-01T00:00:00Z' });

type App = Awaited<ReturnType<typeof mountApp>>;

/** A cold launch with a stored refresh token whose refresh stays pending until released. */
async function launchRestoring(url: string) {
  const refresh = deferred();
  const api = stubApi({ [REFRESH]: () => refresh.promise, [PREVIEW]: preview });
  const app = await mountApp(url, (store) => {
    store.__control.seeded.refresh = REFRESH_1;
  });
  await app.lib.waitFor(() => expect(api.to(REFRESH)).toHaveLength(1));
  expect(sessionStore().getState().status).toBe('restoring');
  return { app, api, refresh };
}

const byLabel = (app: App, label: RegExp) => app.screen.getByLabelText(label, ALL);

async function expectRestoredSessionKept(app: App, api: ReturnType<typeof stubApi>) {
  await app.lib.waitFor(() => expect(sessionStore().getState().status).toBe('signed-in'));
  expect(sessionStore().getState().accessToken).toBe(ACCESS_2);
  expect(api.to(LOGIN)).toHaveLength(0);
  expect(api.to(REGISTER)).toHaveLength(0);
  expect(app.secureStore.deleteItemAsync).not.toHaveBeenCalled();
  expect(app.secureStore.setItemAsync.mock.calls.map(([, v]) => v)).toEqual(
    expect.arrayContaining([ACCESS_2, REFRESH_2]),
  );
}

describe('M1: sign-in cannot submit while restoring', () => {
  it('sends no login request, and the restored session is not undone when the refresh succeeds', async () => {
    const { app, api, refresh } = await launchRestoring('/sign-in');
    await app.fireEvent.changeText(byLabel(app, /username/i), 'ada');
    await app.fireEvent.changeText(byLabel(app, /password/i), 'correct horse battery');
    await app.fireEvent.press(app.screen.getByRole('button', { name: 'Sign in', ...ALL }));
    await app.fireEvent(byLabel(app, /password/i), 'submitEditing');
    expect(api.to(LOGIN)).toHaveLength(0);
    await app.lib.act(async () => refresh.release(tokens(ACCESS_2, REFRESH_2)));
    await expectRestoredSessionKept(app, api);
    expect(await app.screen.findByLabelText('Play')).toBeTruthy();
  });
});

describe('M1: registration cannot submit while restoring', () => {
  it('sends no register request, and the restored session is not undone when the refresh succeeds', async () => {
    const { app, api, refresh } = await launchRestoring('/invite/tok-123');
    await app.screen.findByText(/Ada Lovelace/, ALL);
    await app.fireEvent.changeText(byLabel(app, /username/i), 'ada');
    await app.fireEvent.changeText(byLabel(app, /display name/i), 'Ada');
    await app.fireEvent.changeText(byLabel(app, /password/i), 'correct horse battery');
    await app.fireEvent.press(
      app.screen.getByRole('button', { name: /register|create account|sign up|join/i, ...ALL }),
    );
    expect(api.to(REGISTER)).toHaveLength(0);
    await app.lib.act(async () => refresh.release(tokens(ACCESS_2, REFRESH_2)));
    await expectRestoredSessionKept(app, api);
  });
});

describe('m1: the screens under the loading view are hidden from assistive technology', () => {
  it('hides the form while restoring and exposes it once the restore ends signed out', async () => {
    const { app, refresh } = await launchRestoring('/sign-in');
    expect(app.screen.queryByLabelText(/username/i)).toBeNull();
    expect(app.screen.queryByRole('button', { name: 'Sign in' })).toBeNull();
    expect(byLabel(app, /username/i)).toBeTruthy(); // still mounted, only hidden
    await app.lib.act(async () =>
      refresh.release(failure(401, 'invalid_refresh', 'Session expired.')),
    );
    expect(await app.screen.findByLabelText(/username/i)).toBeTruthy();
  });
});
