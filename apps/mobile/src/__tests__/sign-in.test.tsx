import { StyleSheet } from 'react-native';
// S-2.1.7 AC1, AC2 (sign-in half): the sign-in screen through the real route tree, with a stubbed
// fetch and an in-memory expo-secure-store.
import {
  ACCESS_1,
  LOGIN,
  REFRESH_1,
  failure,
  isAnnounced,
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

type App = Awaited<ReturnType<typeof mountApp>>;

const username = (app: App) => app.screen.getByLabelText(/username/i);
const password = (app: App) => app.screen.getByLabelText(/password/i);
const signInButton = (app: App) => app.screen.getByRole('button', { name: 'Sign in' });

async function openSignIn() {
  const app = await mountApp('/sign-in');
  await app.screen.findByLabelText(/username/i);
  return app;
}

async function submitCredentials(app: App, pass = 'correct horse battery') {
  await app.fireEvent.changeText(username(app), 'ada');
  await app.fireEvent.changeText(password(app), pass);
  await app.fireEvent.press(signInButton(app));
}

describe('AC1: signing in', () => {
  it('posts the credentials in mobile mode and lands on the /play tab', async () => {
    const api = stubApi({ [LOGIN]: () => tokens() });
    const app = await openSignIn();
    await submitCredentials(app);
    expect(await app.screen.findByLabelText('Play')).toBeTruthy();
    expect(app.screen).toHavePathname('/play');
    expect(api.to(LOGIN)).toHaveLength(1);
    expect(api.to(LOGIN)[0]!.method).toBe('POST');
    expect(api.to(LOGIN)[0]!.body).toEqual({ username: 'ada', password: 'correct horse battery' });
    expect(api.to(LOGIN)[0]!.headers.get('X-Client')).toBe('mobile');
  });

  it('stores both tokens in expo-secure-store under an access and a refresh key', async () => {
    stubApi({ [LOGIN]: () => tokens() });
    const app = await openSignIn();
    await submitCredentials(app);
    await app.screen.findByLabelText('Play');
    const writes = app.secureStore.setItemAsync.mock.calls;
    const access = writes.find(([, value]) => value === ACCESS_1);
    const refresh = writes.find(([, value]) => value === REFRESH_1);
    expect(access).toBeDefined();
    expect(refresh).toBeDefined();
    expect(access![0]).toMatch(/access/i);
    expect(refresh![0]).toMatch(/refresh/i);
    expect(access![0]).not.toBe(refresh![0]);
  });

  it('submits when the return key is pressed in the password field', async () => {
    const api = stubApi({ [LOGIN]: () => tokens() });
    const app = await openSignIn();
    await app.fireEvent.changeText(username(app), 'ada');
    await app.fireEvent.changeText(password(app), 'correct horse battery');
    await app.fireEvent(password(app), 'submitEditing');
    expect(await app.screen.findByLabelText('Play')).toBeTruthy();
    expect(api.to(LOGIN)).toHaveLength(1);
  });

  it('shows a masked password field with a label on both inputs', async () => {
    stubApi();
    const app = await openSignIn();
    expect(password(app).props.secureTextEntry).toBe(true);
    expect(username(app).props.secureTextEntry).toBeFalsy();
    expect(app.screen.getByText(/username/i)).toBeTruthy();
    expect(app.screen.getByText(/password/i)).toBeTruthy();
  });

  it('links to the "I have an invite" screen', async () => {
    stubApi();
    const app = await openSignIn();
    await app.fireEvent.press(app.screen.getByLabelText(/i have an invite/i));
    await app.screen.findByLabelText(/invite/i);
    expect(app.screen).toHavePathname('/have-invite');
  });

  it('keeps the touch target of the sign-in button at 44 pt or more', async () => {
    stubApi();
    const app = await openSignIn();
    const style = StyleSheet.flatten(signInButton(app).props.style);
    expect(style.minHeight).toBeGreaterThanOrEqual(44);
    expect(style.minWidth).toBeGreaterThanOrEqual(44);
  });
});

describe('AC1: wrong credentials and rate limiting show the same messages as web', () => {
  it('shows the envelope message for a 401, announced, and clears the password', async () => {
    const api = stubApi({
      [LOGIN]: () => failure(401, 'invalid_credentials', 'Incorrect username or password.'),
    });
    const app = await openSignIn();
    await submitCredentials(app, 'wrong password!!');
    const message = await app.screen.findByText('Incorrect username or password.');
    expect(isAnnounced(message)).toBe(true);
    expect(password(app).props.value).toBe('');
    expect(username(app).props.value).toBe('ada');
    expect(app.screen).toHavePathname('/sign-in');
    expect(api.to(LOGIN)).toHaveLength(1);
    expect(app.secureStore.setItemAsync).not.toHaveBeenCalled();
  });

  it('can sign in after a failed attempt', async () => {
    let attempt = 0;
    stubApi({
      [LOGIN]: () =>
        ++attempt === 1
          ? failure(401, 'invalid_credentials', 'Incorrect username or password.')
          : tokens(),
    });
    const app = await openSignIn();
    await submitCredentials(app, 'wrong password!!');
    await app.screen.findByText('Incorrect username or password.');
    await app.fireEvent.changeText(password(app), 'correct horse battery');
    await app.fireEvent.press(signInButton(app));
    expect(await app.screen.findByLabelText('Play')).toBeTruthy();
  });

  it.each([
    ['120', /^Too many attempts\. Try again in 2 minutes\.?$/],
    ['30', /^Too many attempts\. Try again in 1 minute\.?$/],
    ['60', /^Too many attempts\. Try again in 1 minute\.?$/],
    ['61', /^Too many attempts\. Try again in 2 minutes\.?$/],
    ['3600', /^Too many attempts\. Try again in 60 minutes\.?$/],
  ])('a 429 with Retry-After %s shows the web minutes text', async (retryAfter, expected) => {
    stubApi({
      [LOGIN]: () => failure(429, 'rate_limited', 'Slow down.', { 'Retry-After': retryAfter }),
    });
    const app = await openSignIn();
    await submitCredentials(app);
    const message = await app.screen.findByText(expected);
    expect(isAnnounced(message)).toBe(true);
  });

  it('a 429 without Retry-After shows the web fallback text', async () => {
    stubApi({ [LOGIN]: () => failure(429, 'rate_limited', 'Slow down.') });
    const app = await openSignIn();
    await submitCredentials(app);
    expect(await app.screen.findByText('Too many attempts. Try again later.')).toBeTruthy();
  });

  it('a 500 shows the generic message', async () => {
    stubApi({ [LOGIN]: () => failure(500, 'internal_error', 'Boom.') });
    const app = await openSignIn();
    await submitCredentials(app);
    expect(await app.screen.findByText('Something went wrong. Try again.')).toBeTruthy();
  });
});

describe('AC2: no network connection', () => {
  it('shows exactly "You\'re offline" when the request fails to reach the server', async () => {
    stubApi({
      [LOGIN]: () => {
        throw new TypeError('Network request failed');
      },
    });
    const app = await openSignIn();
    await submitCredentials(app);
    const message = await app.screen.findByText("You're offline");
    expect(isAnnounced(message)).toBe(true);
    expect(app.screen.queryByText('Something went wrong. Try again.')).toBeNull();
    expect(app.screen).toHavePathname('/sign-in');
  });
});
