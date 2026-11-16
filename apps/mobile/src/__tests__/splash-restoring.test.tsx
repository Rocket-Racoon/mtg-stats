// S-1.7.3 AC1 (T-1.7.3.4): `restoring` keeps the native splash up, leaving it hides the splash, and
// the sign-in stack is never exposed to a signed-in user's relaunch. Uses the SessionProvider seam
// and the Zustand store as the fake session.
import * as lib from 'expo-router/testing-library';
import * as SplashScreen from 'expo-splash-screen';
import { AppProviders } from '../components/AppProviders';
import { RootNavigator } from '../app/_layout';
import { SessionProvider } from '../session/SessionProvider';
import { useSessionStore, type SessionStatus } from '../session/store';

jest.mock('expo-splash-screen', () => ({
  preventAutoHideAsync: jest.fn(() => Promise.resolve(true)),
  hideAsync: jest.fn(() => Promise.resolve()),
}));
jest.setTimeout(60000);

const hideAsync = jest.mocked(SplashScreen.hideAsync);

function launch(initialStatus: SessionStatus, url = '/') {
  return lib.renderRouter(
    {
      appDir: './src/app',
      overrides: {
        _layout: () => (
          <AppProviders>
            <SessionProvider initialStatus={initialStatus}>
              <RootNavigator />
            </SessionProvider>
          </AppProviders>
        ),
      },
    },
    { initialUrl: url },
  );
}

const setStatus = (status: SessionStatus) =>
  lib.act(async () => {
    useSessionStore.getState().setStatus(status);
  });

beforeEach(() => {
  hideAsync.mockClear();
});

describe('splash while the session is restoring', () => {
  it('does not hide the splash while the status is restoring', async () => {
    await launch('restoring');
    await lib.act(async () => undefined);
    expect(hideAsync).not.toHaveBeenCalled();
  });

  it('hides the splash once the status becomes signed-in', async () => {
    await launch('restoring');
    expect(hideAsync).not.toHaveBeenCalled();
    await setStatus('signed-in');
    expect(hideAsync).toHaveBeenCalledTimes(1);
  });

  it('hides the splash once the status becomes signed-out', async () => {
    await launch('restoring');
    await setStatus('signed-out');
    expect(hideAsync).toHaveBeenCalledTimes(1);
  });

  it('hides the splash for a launch that starts already signed in', async () => {
    await launch('signed-in');
    expect(hideAsync).toHaveBeenCalled();
  });

  it('does not hide the splash again on later status changes between settled states', async () => {
    await launch('restoring');
    await setStatus('signed-out');
    await setStatus('signed-in');
    expect(hideAsync).toHaveBeenCalledTimes(1);
  });
});

describe('a relaunch of a signed-in user', () => {
  it('has no sign-in form rendered at the moment the splash is hidden on restoring to signed-in', async () => {
    await launch('restoring');
    const atHide: boolean[] = [];
    hideAsync.mockImplementationOnce(() => {
      atHide.push(
        lib.screen.queryByLabelText(/username/i, { includeHiddenElements: true }) !== null ||
          lib.screen.queryByLabelText('I have an invite', { includeHiddenElements: true }) !== null,
      );
      return Promise.resolve();
    });
    await setStatus('signed-in');
    expect(await lib.screen.findByLabelText('Play')).toBeTruthy();
    expect(atHide).toEqual([false]);
    expect(lib.screen).toHavePathname('/play');
  });
});
