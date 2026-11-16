// S-2.1.7 AC7: the app's API client uses the secure session store as its token store, in body mode.
import {
  ACCESS_1,
  ACCESS_2,
  LOGIN,
  REFRESH,
  REFRESH_1,
  REFRESH_2,
  failure,
  json,
  mountApp,
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

async function signIn(app: App) {
  await app.fireEvent.changeText(await app.screen.findByLabelText(/username/i), 'ada');
  await app.fireEvent.changeText(app.screen.getByLabelText(/password/i), 'correct horse battery');
  await app.fireEvent.press(app.screen.getByRole('button', { name: 'Sign in' }));
  await app.screen.findByLabelText('Play');
}

describe('AC7: requests after sign-in', () => {
  it('carry the Bearer access token and X-Client: mobile', async () => {
    const api = stubApi({ [LOGIN]: () => tokens() });
    const app = await mountApp('/sign-in');
    await signIn(app);
    await app.lib.act(async () => {
      await app.api.GET(LIVE);
    });
    const live = api.to(LIVE);
    expect(live).toHaveLength(1);
    expect(live[0]!.headers.get('Authorization')).toBe(`Bearer ${ACCESS_1}`);
    expect(live[0]!.headers.get('X-Client')).toBe('mobile');
  });

  it('on a 401 refresh with the stored refresh token in the body, rotate both tokens, and retry', async () => {
    let liveCalls = 0;
    const api = stubApi({
      [LOGIN]: () => tokens(ACCESS_1, REFRESH_1),
      [REFRESH]: () => tokens(ACCESS_2, REFRESH_2),
      [LIVE]: () =>
        ++liveCalls === 1 ? failure(401, 'token_expired', 'Expired.') : json({ status: 'ok' }),
    });
    const app = await mountApp('/sign-in');
    await signIn(app);
    await app.lib.act(async () => {
      await app.api.GET(LIVE);
    });
    expect(api.to(REFRESH)).toHaveLength(1);
    expect(api.to(REFRESH)[0]!.body).toEqual({ refresh_token: REFRESH_1 });
    expect(api.to(REFRESH)[0]!.headers.get('X-Client')).toBe('mobile');
    const live = api.to(LIVE);
    expect(live).toHaveLength(2);
    expect(live[0]!.headers.get('Authorization')).toBe(`Bearer ${ACCESS_1}`);
    expect(live[1]!.headers.get('Authorization')).toBe(`Bearer ${ACCESS_2}`);
    const writes = app.secureStore.setItemAsync.mock.calls;
    expect(writes.find(([k, v]) => v === ACCESS_2 && /access/i.test(k))).toBeDefined();
    expect(writes.find(([k, v]) => v === REFRESH_2 && /refresh/i.test(k))).toBeDefined();
    // The next request already uses the rotated token, with no second refresh.
    await app.lib.act(async () => {
      await app.api.GET(LIVE);
    });
    expect(api.to(LIVE)[2]!.headers.get('Authorization')).toBe(`Bearer ${ACCESS_2}`);
    expect(api.to(REFRESH)).toHaveLength(1);
  });

  it('on a rejected refresh sign the user out, delete both stored tokens and show sign-in', async () => {
    stubApi({
      [LOGIN]: () => tokens(),
      [REFRESH]: () => failure(401, 'invalid_refresh', 'Session expired.'),
      [LIVE]: () => failure(401, 'token_expired', 'Expired.'),
    });
    const app = await mountApp('/sign-in');
    await signIn(app);
    await app.lib.act(async () => {
      await app.api.GET(LIVE).catch(() => undefined);
    });
    expect(await app.screen.findByLabelText(/username/i)).toBeTruthy();
    expect(app.screen.queryByLabelText('Play')).toBeNull();
    const deleted = app.secureStore.deleteItemAsync.mock.calls.map(([k]) => k);
    expect(deleted.some((k) => /access/i.test(k))).toBe(true);
    expect(deleted.some((k) => /refresh/i.test(k))).toBe(true);
  });
});

describe('decision 5: a secure-store write failure does not end the session', () => {
  it('signs in, lands on /play and keeps sending the Bearer token when the write throws', async () => {
    const api = stubApi({ [LOGIN]: () => tokens() });
    const app = await mountApp('/sign-in', (store) => {
      store.__control.failWrite = true;
    });
    await signIn(app);
    expect(app.screen).toHavePathname('/play');
    await app.lib.act(async () => {
      await app.api.GET(LIVE);
    });
    expect(api.to(LIVE)[0]!.headers.get('Authorization')).toBe(`Bearer ${ACCESS_1}`);
  });

  it('keeps the new tokens in memory across a refresh when the write throws', async () => {
    let liveCalls = 0;
    const api = stubApi({
      [LOGIN]: () => tokens(ACCESS_1, REFRESH_1),
      [REFRESH]: () => tokens(ACCESS_2, REFRESH_2),
      [LIVE]: () =>
        ++liveCalls === 1 ? failure(401, 'token_expired', 'Expired.') : json({ status: 'ok' }),
    });
    const app = await mountApp('/sign-in', (store) => {
      store.__control.failWrite = true;
    });
    await signIn(app);
    await app.lib.act(async () => {
      await app.api.GET(LIVE);
    });
    expect(api.to(LIVE)[1]!.headers.get('Authorization')).toBe(`Bearer ${ACCESS_2}`);
    expect(app.screen.queryByLabelText(/username/i)).toBeNull();
  });
});
