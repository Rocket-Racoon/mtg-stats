// S-2.1.7 review M2 and m6: any protected path opened cold is remembered across sign-in (signed out)
// or reached directly after the restore (signed in); public paths are never remembered; the restore
// runs once per process.
import {
  ACCESS_2,
  LOGIN,
  PREVIEW,
  REFRESH,
  REFRESH_1,
  REFRESH_2,
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

type App = Awaited<ReturnType<typeof mountApp>>;

async function signInFromScreen(app: App) {
  await app.fireEvent.changeText(await app.screen.findByLabelText(/username/i), 'ada');
  await app.fireEvent.changeText(app.screen.getByLabelText(/password/i), 'correct horse battery');
  await app.fireEvent.press(app.screen.getByRole('button', { name: 'Sign in' }));
}

describe('M2: a protected tab route opened cold while signed out', () => {
  it('goes to sign-in, and after signing in lands on /decks, not /play', async () => {
    stubApi({ [LOGIN]: () => tokens() });
    const app = await mountApp('/decks');
    expect(await app.screen.findByLabelText(/username/i)).toBeTruthy();
    expect(app.screen).toHavePathname('/sign-in');
    await signInFromScreen(app);
    expect(await app.screen.findByRole('header', { name: 'Decks' })).toBeTruthy();
    expect(app.screen).toHavePathname('/decks');
    expect(sessionStore().getState().pendingHref).toBeNull();
  });

  it('lands on /stats for a /stats link in the same way', async () => {
    stubApi({ [LOGIN]: () => tokens() });
    const app = await mountApp('/stats');
    await signInFromScreen(app);
    expect(await app.screen.findByRole('header', { name: 'Stats' })).toBeTruthy();
    expect(app.screen).toHavePathname('/stats');
  });
});

describe('M2: a protected route opened cold while signed in', () => {
  it('lands on /decks, not /play, once the restore succeeds', async () => {
    stubApi({ [REFRESH]: () => tokens(ACCESS_2, REFRESH_2) });
    const app = await mountApp('/decks', (store) => {
      store.__control.seeded.refresh = REFRESH_1;
    });
    expect(await app.screen.findByRole('header', { name: 'Decks' })).toBeTruthy();
    expect(app.screen).toHavePathname('/decks');
  });
});

describe('M2: public paths are never made pending', () => {
  const preview = () =>
    json({ inviter_display_name: 'Ada Lovelace', expires_at: '2030-01-01T00:00:00Z' });

  it.each([
    ['an invite link', '/invite/tok-1'],
    ['a reset link', '/reset/tok-1'],
    ['the sign-in screen', '/sign-in'],
    ['the I-have-an-invite screen', '/have-invite'],
  ])('does not remember %s opened signed out', async (_label, url) => {
    stubApi({ [PREVIEW]: preview });
    const app = await mountApp(url);
    await app.lib.waitFor(() => expect(sessionStore().getState().status).toBe('signed-out'));
    expect(sessionStore().getState().pendingHref).toBeNull();
    expect(app.screen).toHavePathname(url);
  });
});

describe('m6: the restore runs once per process', () => {
  it('does not refresh again when the app tree is remounted after the restore finished', async () => {
    const api = stubApi({ [REFRESH]: () => tokens(ACCESS_2, REFRESH_2) });
    const app = await mountApp('/', (store) => {
      store.__control.seeded.refresh = REFRESH_1;
    });
    expect(await app.screen.findByLabelText('Play')).toBeTruthy();
    expect(api.to(REFRESH)).toHaveLength(1);

    await app.lib.cleanup();
    const second = await app.lib.renderRouter('./src/app', { initialUrl: '/' });
    expect(await second.findByLabelText('Play')).toBeTruthy();
    expect(sessionStore().getState().status).toBe('signed-in');
    expect(api.to(REFRESH)).toHaveLength(1);
  });
});
