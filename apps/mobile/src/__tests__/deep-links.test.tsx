// S-2.1.7 AC4 and AC5: the invite and reset deep links work signed out, and a protected deep link
// opened signed out survives sign-in. The URLs below are what mtgstats://<path> resolves to.
import {
  LOGIN,
  PREVIEW,
  REGISTER,
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

afterEach(async () => {
  await unmountApp();
  jest.restoreAllMocks();
});

const preview = () =>
  json({ inviter_display_name: 'Ada Lovelace', expires_at: '2030-01-01T00:00:00Z' });

type App = Awaited<ReturnType<typeof mountApp>>;

async function signInFromScreen(app: App) {
  await app.fireEvent.changeText(await app.screen.findByLabelText(/username/i), 'ada');
  await app.fireEvent.changeText(app.screen.getByLabelText(/password/i), 'correct horse battery');
  await app.fireEvent.press(app.screen.getByRole('button', { name: 'Sign in' }));
}

describe('AC4: mtgstats://invite/<token> and mtgstats://reset/<token> while signed out', () => {
  it('opens the invite screen with the token filled in and no redirect to sign-in', async () => {
    const api = stubApi({ [PREVIEW]: preview });
    const app = await mountApp('/invite/tok-9f2');
    expect(await app.screen.findByText(/Ada Lovelace/)).toBeTruthy();
    expect(app.screen).toHavePathname('/invite/tok-9f2');
    expect(api.to(PREVIEW)[0]!.body).toEqual({ token: 'tok-9f2' });
    expect(app.screen.getByLabelText(/username/i)).toBeTruthy();
  });

  it('opens the reset screen showing the token, with no redirect to sign-in', async () => {
    stubApi();
    const app = await mountApp('/reset/rst-77a');
    expect(await app.screen.findByTestId('reset-screen')).toBeTruthy();
    expect(app.screen.getByTestId('reset-screen-value')).toHaveTextContent('rst-77a');
    expect(app.screen).toHavePathname('/reset/rst-77a');
    expect(app.screen.queryByLabelText(/username/i)).toBeNull();
  });
});

describe('AC5: a protected deep link opened signed out', () => {
  it('goes to sign-in, and after signing in lands on the screen the link pointed to', async () => {
    stubApi({ [LOGIN]: () => tokens() });
    const app = await mountApp('/join/ABC123');
    expect(await app.screen.findByLabelText(/username/i)).toBeTruthy();
    expect(app.screen).toHavePathname('/sign-in');
    expect(app.screen.queryByTestId('join-screen')).toBeNull();
    await signInFromScreen(app);
    expect(await app.screen.findByTestId('join-screen')).toBeTruthy();
    expect(app.screen.getByTestId('join-screen-value')).toHaveTextContent('ABC123');
    expect(app.screen).toHavePathname('/join/ABC123');
  });

  it('keeps the pending link across a failed attempt', async () => {
    let attempt = 0;
    stubApi({
      [LOGIN]: () =>
        ++attempt === 1
          ? failure(401, 'invalid_credentials', 'Incorrect username or password.')
          : tokens(),
    });
    const app = await mountApp('/join/ABC123');
    await signInFromScreen(app);
    await app.screen.findByText('Incorrect username or password.');
    await app.fireEvent.changeText(app.screen.getByLabelText(/password/i), 'correct horse battery');
    await app.fireEvent.press(app.screen.getByRole('button', { name: 'Sign in' }));
    expect(await app.screen.findByTestId('join-screen')).toBeTruthy();
    expect(app.screen).toHavePathname('/join/ABC123');
  });

  it('keeps the pending link across registration from an invite', async () => {
    stubApi({ [PREVIEW]: preview, [REGISTER]: () => tokens(undefined, undefined, 201) });
    const app = await mountApp('/join/ABC123');
    await app.fireEvent.press(await app.screen.findByLabelText(/i have an invite/i));
    const field = await app.screen.findByLabelText(/invite/i);
    await app.fireEvent.changeText(field, 'inv-token-123');
    await app.fireEvent(field, 'submitEditing');
    await app.screen.findByText(/Ada Lovelace/);
    await app.fireEvent.changeText(app.screen.getByLabelText(/username/i), 'ada');
    await app.fireEvent.changeText(app.screen.getByLabelText(/display name/i), 'Ada');
    await app.fireEvent.changeText(app.screen.getByLabelText(/password/i), 'correct horse battery');
    await app.fireEvent.press(
      app.screen.getByRole('button', { name: /register|create account|sign up|join/i }),
    );
    expect(await app.screen.findByTestId('join-screen')).toBeTruthy();
    expect(app.screen).toHavePathname('/join/ABC123');
  });

  it('lands on /play when the person went straight to sign-in', async () => {
    stubApi({ [LOGIN]: () => tokens() });
    const app = await mountApp('/sign-in');
    await signInFromScreen(app);
    expect(await app.screen.findByLabelText('Play')).toBeTruthy();
    expect(app.screen).toHavePathname('/play');
  });

  it('does not reopen the old pending link on a later sign-in', async () => {
    stubApi({ [LOGIN]: () => tokens() });
    const app = await mountApp('/join/ABC123');
    await signInFromScreen(app);
    await app.screen.findByTestId('join-screen');
    // Sign out through the More tab is not reachable from the join screen: go back to the app.
    await app.fireEvent.press(app.screen.getByLabelText('Back to app'));
    await app.fireEvent.press(await app.screen.findByLabelText('More'));
    await app.fireEvent.press(await app.screen.findByLabelText('Sign out'));
    await signInFromScreen(app);
    expect(await app.screen.findByLabelText('Play')).toBeTruthy();
    expect(app.screen).toHavePathname('/play');
  });
});
