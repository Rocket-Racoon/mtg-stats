// S-1.7.3 AC2 gap: a protected link with URL-encoded characters, opened signed out, opens the same
// screen with the decoded value once the status becomes signed-in.
import * as lib from 'expo-router/testing-library';
import { AppProviders } from '../components/AppProviders';
import { RootNavigator } from '../app/_layout';
import { SessionProvider } from '../session/SessionProvider';
import { useSessionStore } from '../session/store';

jest.mock('expo-splash-screen', () => ({
  preventAutoHideAsync: jest.fn(() => Promise.resolve(true)),
  hideAsync: jest.fn(() => Promise.resolve()),
}));
jest.setTimeout(60000);

describe('pending deep link with encoded parameters', () => {
  it('opens /join/AB%20C%2F1 after sign-in, showing the decoded code', async () => {
    await lib.renderRouter(
      {
        appDir: './src/app',
        overrides: {
          _layout: () => (
            <AppProviders>
              <SessionProvider initialStatus="signed-out">
                <RootNavigator />
              </SessionProvider>
            </AppProviders>
          ),
        },
      },
      { initialUrl: '/join/AB%20C%2F1' },
    );
    expect(await lib.screen.findByLabelText('I have an invite')).toBeTruthy();
    expect(lib.screen.queryByTestId('join-screen')).toBeNull();
    await lib.act(async () => {
      await useSessionStore.getState().signIn({
        accessToken: 'test-access-token', // secrets-check: allow
        refreshToken: 'test-refresh-token', // secrets-check: allow
      });
    });
    expect(await lib.screen.findByTestId('join-screen-value')).toHaveTextContent('AB C/1');
    expect(useSessionStore.getState().pendingHref).toBeNull();
  });
});
