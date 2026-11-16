import { AccessibilityInfo, TextInput } from 'react-native';
// S-2.1.8 (phase C, decision 7): the change-password screen on the More tab, through the real route
// tree with a stubbed fetch and an in-memory expo-secure-store.
import {
  ACCESS_1,
  ACCESS_2,
  REFRESH_1,
  REFRESH_2,
  deferred,
  failure,
  isAnnounced,
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

const CHANGE = '/api/v1/auth/change-password';
const CURRENT = 'old password 123'; // secrets-check: allow
const NEW = 'brand new password'; // secrets-check: allow
const OFFLINE = "You're offline";
const MISMATCH = "Passwords don't match.";

afterEach(async () => {
  await unmountApp();
  jest.restoreAllMocks();
});

type App = Awaited<ReturnType<typeof mountApp>>;

const current = (app: App) => app.screen.getByLabelText('Current password');
const next = (app: App) => app.screen.getByLabelText('New password');
const confirm = (app: App) => app.screen.getByLabelText('Confirm new password');
/** The submit button: the More tab's button of the same name may still be mounted below it. */
const submit = (app: App) => {
  const all = app.screen.getAllByRole('button', { name: /^change password$|^changing password/i });
  return all[all.length - 1]!;
};

/**
 * Records `focus()` calls on the (jest-mocked) TextInput class and returns the testID of the
 * input that was focused last, or null when none was focused since the spy was installed.
 */
function spyOnFocus() {
  const spy = jest.spyOn(TextInput.prototype, 'focus').mockImplementation(() => undefined);
  return {
    spy,
    lastFocused: (): string | null => {
      const contexts = spy.mock.contexts as { props?: { testID?: string } }[];
      return contexts.at(-1)?.props?.testID ?? null;
    },
  };
}

async function signedInAtMore() {
  const app = await mountApp('/');
  await app.screen.findByLabelText('I have an invite');
  await app.lib.act(async () => {
    await sessionStore().getState().signIn({ accessToken: ACCESS_1, refreshToken: REFRESH_1 });
  });
  await app.fireEvent.press(await app.screen.findByLabelText('More'));
  return app;
}

async function openScreen() {
  const app = await signedInAtMore();
  await app.fireEvent.press(await app.screen.findByRole('button', { name: 'Change password' }));
  await app.screen.findByLabelText('Current password');
  return app;
}

async function fill(app: App, cur = CURRENT, pass = NEW, conf = pass) {
  await app.fireEvent.changeText(current(app), cur);
  await app.fireEvent.changeText(next(app), pass);
  await app.fireEvent.changeText(confirm(app), conf);
}

describe('the More tab entry', () => {
  it('has a "Change password" button that opens /more/change-password', async () => {
    stubApi();
    const app = await signedInAtMore();
    await app.fireEvent.press(await app.screen.findByRole('button', { name: 'Change password' }));
    expect(await app.screen.findByLabelText('Current password')).toBeTruthy();
    expect(app.screen).toHavePathname('/more/change-password');
  });
});

describe('the screen', () => {
  it('shows three labelled masked fields', async () => {
    stubApi();
    const app = await openScreen();
    for (const field of [current(app), next(app), confirm(app)]) {
      expect(field.props.secureTextEntry).toBe(true);
    }
    expect(app.screen.getByText('Current password')).toBeTruthy();
    expect(app.screen.getByText('New password')).toBeTruthy();
    expect(app.screen.getByText('Confirm new password')).toBeTruthy();
  });
});

describe('client checks send no request', () => {
  it('announces each required field next to it when all are empty', async () => {
    const api = stubApi();
    const app = await openScreen();
    await app.fireEvent.press(submit(app));
    const message = await app.screen.findByText(/current password is required/i);
    expect(isAnnounced(message)).toBe(true);
    expect(app.screen.getAllByText(/is required/i).length).toBeGreaterThanOrEqual(3);
    expect(api.to(CHANGE)).toHaveLength(0);
  });

  it('rejects a new password under 12 code points', async () => {
    const api = stubApi();
    const app = await openScreen();
    await fill(app, CURRENT, 'elevenchars');
    await app.fireEvent.press(submit(app));
    const message = await app.screen.findByText(/12 to 128 characters/i);
    expect(isAnnounced(message)).toBe(true);
    expect(api.to(CHANGE)).toHaveLength(0);
  });

  it('counts code points, not UTF-16 units (11 emoji are too short)', async () => {
    const api = stubApi();
    const app = await openScreen();
    await fill(app, CURRENT, '😀'.repeat(11));
    await app.fireEvent.press(submit(app));
    await app.screen.findByText(/12 to 128 characters/i);
    expect(api.to(CHANGE)).toHaveLength(0);
  });

  it('announces "Passwords don\'t match." when the confirmation differs', async () => {
    const api = stubApi();
    const app = await openScreen();
    await fill(app, CURRENT, NEW, NEW + 'x');
    await app.fireEvent.press(submit(app));
    const message = await app.screen.findByText(MISMATCH);
    expect(isAnnounced(message)).toBe(true);
    expect(api.to(CHANGE)).toHaveLength(0);
  });
});

describe('success', () => {
  it('posts the body with Bearer and X-Client mobile', async () => {
    const api = stubApi({ [CHANGE]: () => tokens(ACCESS_2, REFRESH_2) });
    const app = await openScreen();
    await fill(app);
    await app.fireEvent.press(submit(app));
    await app.lib.waitFor(() => expect(api.to(CHANGE)).toHaveLength(1));
    const call = api.to(CHANGE)[0]!;
    expect(call.method).toBe('POST');
    expect(call.body).toEqual({ current_password: CURRENT, new_password: NEW });
    expect(call.headers.get('Authorization')).toBe(`Bearer ${ACCESS_1}`);
    expect(call.headers.get('X-Client')).toBe('mobile');
  });

  it('stores BOTH new tokens in the secure store and the session store', async () => {
    stubApi({ [CHANGE]: () => tokens(ACCESS_2, REFRESH_2) });
    const app = await openScreen();
    await fill(app);
    await app.fireEvent.press(submit(app));
    await app.lib.waitFor(() => expect(sessionStore().getState().accessToken).toBe(ACCESS_2));
    expect(sessionStore().getState().refreshToken).toBe(REFRESH_2);
    expect(sessionStore().getState().status).toBe('signed-in');
    const writes = app.secureStore.setItemAsync.mock.calls;
    const access = writes.find(([, value]) => value === ACCESS_2);
    const refresh = writes.find(([, value]) => value === REFRESH_2);
    expect(access).toBeDefined();
    expect(refresh).toBeDefined();
    expect(access![0]).toMatch(/access/i);
    expect(refresh![0]).toMatch(/refresh/i);
  });

  it('stays on the screen, clears the fields and shows the announced status', async () => {
    const announce = jest
      .spyOn(AccessibilityInfo, 'announceForAccessibility')
      .mockImplementation(() => undefined);
    stubApi({ [CHANGE]: () => tokens(ACCESS_2, REFRESH_2) });
    const app = await openScreen();
    await fill(app);
    await app.fireEvent.press(submit(app));
    const status = await app.screen.findByTestId('change-password-status');
    expect(status).toHaveTextContent('Password changed.');
    expect(isAnnounced(status)).toBe(true);
    expect(announce).toHaveBeenCalledWith('Password changed.');
    expect(app.screen).toHavePathname('/more/change-password');
    expect(current(app).props.value).toBe('');
    expect(next(app).props.value).toBe('');
    expect(confirm(app).props.value).toBe('');
  });

  it('shows the success status as a polite status, not an alert, announced exactly once', async () => {
    const announce = jest
      .spyOn(AccessibilityInfo, 'announceForAccessibility')
      .mockImplementation(() => undefined);
    stubApi({ [CHANGE]: () => tokens(ACCESS_2, REFRESH_2) });
    const app = await openScreen();
    await fill(app);
    await app.fireEvent.press(submit(app));
    const status = await app.screen.findByTestId('change-password-status');
    expect(status).toHaveTextContent('Password changed.');
    expect(status.props.role).toBe('status');
    expect(status.props.role).not.toBe('alert');
    expect(status.props.accessibilityRole).not.toBe('alert');
    expect(status.props.accessibilityLiveRegion).toBe('polite');
    expect(announce).toHaveBeenCalledTimes(1);
    expect(announce).toHaveBeenCalledWith('Password changed.');
  });

  it('clears the status on the next submit', async () => {
    stubApi({ [CHANGE]: () => tokens(ACCESS_2, REFRESH_2) });
    const app = await openScreen();
    await fill(app);
    await app.fireEvent.press(submit(app));
    await app.screen.findByTestId('change-password-status');
    await app.fireEvent.press(submit(app)); // empty fields: client checks fail
    await app.screen.findByText(/current password is required/i);
    expect(app.screen.queryByTestId('change-password-status')).toBeNull();
  });

  it('a 200 without a refresh token shows the generic error and signs nothing in', async () => {
    stubApi({
      [CHANGE]: () => json({ access_token: ACCESS_2, token_type: 'bearer', expires_in: 900 }),
    });
    const app = await openScreen();
    await fill(app);
    await app.fireEvent.press(submit(app));
    expect(await app.screen.findByText('Something went wrong. Try again.')).toBeTruthy();
    expect(sessionStore().getState().accessToken).toBe(ACCESS_1);
    expect(sessionStore().getState().refreshToken).toBe(REFRESH_1);
    expect(app.screen.queryByTestId('change-password-status')).toBeNull();
  });
});

describe('errors', () => {
  it('a 400 puts the message on the current-password field and clears it', async () => {
    const message = 'Current password is incorrect.';
    const api = stubApi({
      [CHANGE]: () =>
        failure(
          400,
          'invalid_current_password',
          message,
          {},
          {
            fields: [{ loc: ['body', 'current_password'], path: 'body.current_password', message }],
          },
        ),
    });
    const app = await openScreen();
    await fill(app);
    await app.fireEvent.press(submit(app));
    const shown = await app.screen.findByText(message);
    expect(isAnnounced(shown)).toBe(true);
    expect(current(app).props.value).toBe('');
    expect(api.to(CHANGE)).toHaveLength(1);
    expect(app.screen).toHavePathname('/more/change-password');
    expect(sessionStore().getState().accessToken).toBe(ACCESS_1);
  });

  it('a 400 moves focus to the current-password field', async () => {
    const message = 'Current password is incorrect.';
    stubApi({
      [CHANGE]: () =>
        failure(
          400,
          'invalid_current_password',
          message,
          {},
          {
            fields: [{ loc: ['body', 'current_password'], path: 'body.current_password', message }],
          },
        ),
    });
    const app = await openScreen();
    await fill(app);
    const focus = spyOnFocus();
    await app.fireEvent.press(submit(app));
    await app.screen.findByText(message);
    expect(focus.lastFocused()).toBe('change-password-current');
  });

  it('a 422 on the new password moves focus to the new-password field', async () => {
    const message = 'Password is too common.';
    stubApi({
      [CHANGE]: () =>
        failure(
          422,
          'validation_error',
          'Invalid.',
          {},
          { fields: [{ loc: ['body', 'new_password'], path: 'body.new_password', message }] },
        ),
    });
    const app = await openScreen();
    await fill(app);
    const focus = spyOnFocus();
    await app.fireEvent.press(submit(app));
    await app.screen.findByText(message);
    expect(focus.lastFocused()).toBe('change-password-new');
  });

  it('a 422 is mapped to the new-password field', async () => {
    const message = 'Password must be at least 12 characters.';
    stubApi({
      [CHANGE]: () =>
        failure(
          422,
          'validation_error',
          'Invalid.',
          {},
          { fields: [{ loc: ['body', 'new_password'], path: 'body.new_password', message }] },
        ),
    });
    const app = await openScreen();
    await fill(app);
    await app.fireEvent.press(submit(app));
    const shown = await app.screen.findByText(message);
    expect(isAnnounced(shown)).toBe(true);
    expect(app.screen).toHavePathname('/more/change-password');
  });

  it('a 429 shows the web minutes text', async () => {
    stubApi({
      [CHANGE]: () => failure(429, 'rate_limited', 'Slow down.', { 'Retry-After': '120' }),
    });
    const app = await openScreen();
    await fill(app);
    await app.fireEvent.press(submit(app));
    const shown = await app.screen.findByText(/^Too many attempts\. Try again in 2 minutes\.?$/);
    expect(isAnnounced(shown)).toBe(true);
  });

  it('a network error shows exactly "You\'re offline"', async () => {
    stubApi({
      [CHANGE]: () => {
        throw new TypeError('Network request failed');
      },
    });
    const app = await openScreen();
    await fill(app);
    await app.fireEvent.press(submit(app));
    const shown = await app.screen.findByText(OFFLINE);
    expect(isAnnounced(shown)).toBe(true);
    expect(app.screen.queryByText('Something went wrong. Try again.')).toBeNull();
  });

  it('a 500 shows the generic message', async () => {
    stubApi({ [CHANGE]: () => failure(500, 'internal_error', 'Boom.') });
    const app = await openScreen();
    await fill(app);
    await app.fireEvent.press(submit(app));
    const shown = await app.screen.findByText('Something went wrong. Try again.');
    expect(isAnnounced(shown)).toBe(true);
    expect(sessionStore().getState().accessToken).toBe(ACCESS_1);
  });
});

describe('pending state', () => {
  it('is busy and disabled while pending, and a second press sends nothing', async () => {
    const held = deferred();
    const api = stubApi({ [CHANGE]: () => held.promise });
    const app = await openScreen();
    await fill(app);
    await app.fireEvent.press(submit(app));
    await app.lib.waitFor(() => expect(api.to(CHANGE)).toHaveLength(1));
    await app.lib.waitFor(() => {
      const state = submit(app).props.accessibilityState;
      expect(state.busy).toBe(true);
      expect(state.disabled).toBe(true);
    });
    await app.fireEvent.press(submit(app));
    expect(api.to(CHANGE)).toHaveLength(1);
    await app.lib.act(async () => {
      held.release(tokens(ACCESS_2, REFRESH_2));
    });
    await app.screen.findByTestId('change-password-status');
    expect(app.screen).toHavePathname('/more/change-password');
    expect(api.to(CHANGE)).toHaveLength(1);
  });
});
