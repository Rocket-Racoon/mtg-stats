// S-2.1.7 AC3 (and AC2, registration half): the "I have an invite" flow and the invite screen,
// through the real route tree, with a stubbed fetch and an in-memory expo-secure-store.
import {
  ACCESS_1,
  PREVIEW,
  REFRESH_1,
  REGISTER,
  failure,
  isAnnounced,
  json,
  mountApp,
  stubApi,
  tokens,
  unmountApp,
  type Handler,
} from './app-harness';

jest.mock('expo-secure-store', () =>
  jest.requireActual<typeof import('./app-harness')>('./app-harness').createSecureStoreMock(),
);
jest.setTimeout(60000);

const TOKEN = 'inv-token-123'; // secrets-check: allow
const GONE_MESSAGE =
  'This invite has expired or was revoked. Ask your playgroup admin for a new one.';
const preview = () =>
  json({ inviter_display_name: 'Ada Lovelace', expires_at: '2030-01-01T00:00:00Z' });
const gone = () => failure(410, 'invite_no_longer_valid', 'Invite no longer valid.');

afterEach(async () => {
  await unmountApp();
  jest.restoreAllMocks();
});

type App = Awaited<ReturnType<typeof mountApp>>;

const input = (app: App, label: RegExp) => app.screen.getByLabelText(label);
const createButton = (app: App) =>
  app.screen.getByRole('button', { name: /register|create account|sign up|join/i });

async function openInvite(handlers: Record<string, Handler> = {}) {
  const api = stubApi({
    [PREVIEW]: preview,
    [REGISTER]: () => tokens(ACCESS_1, REFRESH_1, 201),
    ...handlers,
  });
  const app = await mountApp(`/invite/${TOKEN}`);
  await app.screen.findByText(/Ada Lovelace/);
  return { app, api };
}

async function fill(
  app: App,
  v: { username?: string; displayName?: string; password?: string; email?: string } = {},
) {
  await app.fireEvent.changeText(input(app, /username/i), v.username ?? 'ada');
  await app.fireEvent.changeText(input(app, /display name/i), v.displayName ?? 'Ada');
  await app.fireEvent.changeText(input(app, /password/i), v.password ?? 'correct horse battery');
  if (v.email) await app.fireEvent.changeText(input(app, /email/i), v.email);
}

describe('AC3: the invite screen', () => {
  it('previews the token from the route and shows the inviter and the form, signed out', async () => {
    const { app, api } = await openInvite();
    expect(api.to(PREVIEW)).toHaveLength(1);
    expect(api.to(PREVIEW)[0]!.method).toBe('POST');
    expect(api.to(PREVIEW)[0]!.body).toEqual({ token: TOKEN });
    expect(app.screen).toHavePathname(`/invite/${TOKEN}`);
    for (const label of [/username/i, /display name/i, /password/i, /email/i]) {
      expect(input(app, label)).toBeTruthy();
    }
    expect(input(app, /password/i).props.secureTextEntry).toBe(true);
  });

  it('shows the exact expired-or-revoked message and no form on a 410 preview', async () => {
    stubApi({ [PREVIEW]: gone });
    const app = await mountApp(`/invite/${TOKEN}`);
    expect(await app.screen.findByText(GONE_MESSAGE)).toBeTruthy();
    expect(app.screen.queryByLabelText(/username/i)).toBeNull();
    expect(app.screen.queryByLabelText(/password/i)).toBeNull();
  });
});

describe('AC3: "I have an invite" accepts a pasted link or a bare token', () => {
  async function paste(text: string) {
    const api = stubApi({ [PREVIEW]: preview });
    const app = await mountApp('/have-invite');
    const field = await app.screen.findByLabelText(/invite/i);
    await app.fireEvent.changeText(field, text);
    await app.fireEvent(field, 'submitEditing');
    return { app, api };
  }

  it('extracts the token from a full https link and shows the preview and the form', async () => {
    const { app, api } = await paste(`https://mtgstats.example/invite/${TOKEN}`);
    expect(await app.screen.findByText(/Ada Lovelace/)).toBeTruthy();
    expect(app.screen).toHavePathname(`/invite/${TOKEN}`);
    expect(api.to(PREVIEW)[0]!.body).toEqual({ token: TOKEN });
    expect(input(app, /username/i)).toBeTruthy();
  });

  it('accepts just the token', async () => {
    const { app, api } = await paste(`  ${TOKEN}  `);
    expect(await app.screen.findByText(/Ada Lovelace/)).toBeTruthy();
    expect(api.to(PREVIEW)[0]!.body).toEqual({ token: TOKEN });
  });

  it('stays on the screen and calls no API for text that is not an invite', async () => {
    const { app, api } = await paste('https://mtgstats.example/reset/abc');
    expect(app.screen).toHavePathname('/have-invite');
    expect(api.to(PREVIEW)).toHaveLength(0);
    expect(app.screen.queryByText(/Ada Lovelace/)).toBeNull();
  });
});

describe('AC3: registering signs the person in', () => {
  it('sends the documented body in mobile mode, stores both tokens and lands on /play', async () => {
    const { app, api } = await openInvite();
    await fill(app, { email: 'ada@example.com' });
    await app.fireEvent.press(createButton(app));
    expect(await app.screen.findByLabelText('Play')).toBeTruthy();
    expect(app.screen).toHavePathname('/play');
    expect(api.to(REGISTER)).toHaveLength(1);
    expect(api.to(REGISTER)[0]!.body).toEqual({
      invite_token: TOKEN, // secrets-check: allow
      username: 'ada',
      password: 'correct horse battery',
      display_name: 'Ada',
      email: 'ada@example.com',
    });
    expect(api.to(REGISTER)[0]!.headers.get('X-Client')).toBe('mobile');
    const writes = app.secureStore.setItemAsync.mock.calls;
    expect(writes.find(([k, v]) => v === ACCESS_1 && /access/i.test(k))).toBeDefined();
    expect(writes.find(([k, v]) => v === REFRESH_1 && /refresh/i.test(k))).toBeDefined();
  });

  it('registers without an email and trims the display name', async () => {
    const { app, api } = await openInvite();
    await fill(app, { displayName: '  Ada  ' });
    await app.fireEvent.press(createButton(app));
    await app.screen.findByLabelText('Play');
    const body = api.to(REGISTER)[0]!.body as Record<string, unknown>;
    expect(body.display_name).toBe('Ada');
    expect(body.email === undefined || body.email === '').toBe(true);
  });
});

describe('AC3: registration errors', () => {
  it('shows the username_taken message from a 409 and stays signed out with the form kept', async () => {
    const { app } = await openInvite({
      [REGISTER]: () => failure(409, 'username_taken', 'That username is already taken.'),
    });
    await fill(app);
    await app.fireEvent.press(createButton(app));
    const message = await app.screen.findByText('That username is already taken.');
    expect(isAnnounced(message)).toBe(true);
    expect(app.screen).toHavePathname(`/invite/${TOKEN}`);
    expect(input(app, /username/i).props.value).toBe('ada');
    expect(app.secureStore.setItemAsync).not.toHaveBeenCalled();
  });

  it('maps a 422 to the field named by the last element of each loc', async () => {
    const { app } = await openInvite({
      [REGISTER]: () =>
        failure(
          422,
          'validation_error',
          'Request validation failed.',
          {},
          {
            fields: [
              {
                loc: ['body', 'username'],
                path: 'body.username',
                message: 'Username is reserved.',
              },
              {
                loc: ['body', 'display_name'],
                path: 'body.display_name',
                message: 'Display name is not allowed.',
              },
            ],
          },
        ),
    });
    await fill(app);
    await app.fireEvent.press(createButton(app));
    expect(await app.screen.findByText('Username is reserved.')).toBeTruthy();
    expect(await app.screen.findByText('Display name is not allowed.')).toBeTruthy();
    expect(app.screen).toHavePathname(`/invite/${TOKEN}`);
  });

  it('replaces the form with the expired-or-revoked message on a 410 at submit', async () => {
    const { app } = await openInvite({ [REGISTER]: gone });
    await fill(app);
    await app.fireEvent.press(createButton(app));
    expect(await app.screen.findByText(GONE_MESSAGE)).toBeTruthy();
    expect(app.screen.queryByLabelText(/username/i)).toBeNull();
    expect(app.secureStore.setItemAsync).not.toHaveBeenCalled();
  });

  it('shows the generic message on a server error and keeps the form', async () => {
    const { app } = await openInvite({ [REGISTER]: () => failure(500, 'internal_error', 'Boom.') });
    await fill(app);
    await app.fireEvent.press(createButton(app));
    expect(await app.screen.findByText('Something went wrong. Try again.')).toBeTruthy();
    expect(input(app, /username/i).props.value).toBe('ada');
  });

  it('shows exactly "You\'re offline" when the registration cannot reach the server', async () => {
    const { app } = await openInvite({
      [REGISTER]: () => {
        throw new TypeError('Network request failed');
      },
    });
    await fill(app);
    await app.fireEvent.press(createButton(app));
    const message = await app.screen.findByText("You're offline");
    expect(isAnnounced(message)).toBe(true);
    expect(app.screen.queryByText('Something went wrong. Try again.')).toBeNull();
    expect(input(app, /username/i).props.value).toBe('ada');
  });

  it.each([
    ['429 with Retry-After 120', 120, /^Too many attempts\. Try again in 2 minutes\.?$/],
    ['429 with Retry-After 30', 30, /^Too many attempts\. Try again in 1 minute\.?$/],
  ])('shows the web rate-limit text for a %s', async (_l, seconds, expected) => {
    const { app } = await openInvite({
      [REGISTER]: () =>
        failure(429, 'rate_limited', 'Slow down.', { 'Retry-After': String(seconds) }),
    });
    await fill(app);
    await app.fireEvent.press(createButton(app));
    expect(await app.screen.findByText(expected)).toBeTruthy();
  });
});

describe('AC3: client checks mirror the API and count code points', () => {
  const EMOJI = '\u{1F600}';

  it.each([
    ['a username shorter than 3 characters', { username: 'ab' }],
    ['a username longer than 24 characters', { username: 'a'.repeat(25) }],
    ['a username with a disallowed character', { username: 'ada lovelace' }],
    ['a password shorter than 12 characters', { password: 'x'.repeat(11) }],
    ['a password longer than 128 characters', { password: 'x'.repeat(129) }],
    ['a display name of only spaces', { displayName: '   ' }],
    ['a display name longer than 32 characters', { displayName: 'd'.repeat(33) }],
    ['a display name of 33 emoji', { displayName: EMOJI.repeat(33) }],
    ['an email longer than 320 characters', { email: `${'e'.repeat(320)}@x.io` }],
  ])('rejects %s before calling the API', async (_label, values) => {
    const { app, api } = await openInvite();
    await fill(app, values);
    await app.fireEvent.press(createButton(app));
    await app.screen.findByLabelText(/username/i);
    expect(api.to(REGISTER)).toHaveLength(0);
    expect(app.screen).toHavePathname(`/invite/${TOKEN}`);
    expect(app.screen.getAllByRole('alert').length).toBeGreaterThan(0);
  });

  it.each([
    ['a 3-character username', { username: 'abc' }],
    ['a 24-character username with . _ -', { username: 'a.b_c-d'.padEnd(24, 'x') }],
    ['a 12-character password', { password: 'x'.repeat(12) }],
    ['a 128-character password', { password: 'x'.repeat(128) }],
    ['a 32-character display name', { displayName: 'd'.repeat(32) }],
    ['a display name of 32 emoji (64 UTF-16 units)', { displayName: EMOJI.repeat(32) }],
    ['a password of 65 emoji (130 UTF-16 units)', { password: EMOJI.repeat(65) }],
  ])('accepts %s at its limit', async (_label, values) => {
    const { app, api } = await openInvite();
    await fill(app, values);
    await app.fireEvent.press(createButton(app));
    await app.screen.findByLabelText('Play');
    expect(api.to(REGISTER)).toHaveLength(1);
  });
});
