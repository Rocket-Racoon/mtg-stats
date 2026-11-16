// S-1.7.3 AC1: the config error screen is shown outside the session, so it hides the splash itself;
// a failing splash API never stops it from rendering.
import { render, screen } from '@testing-library/react-native';
import * as SplashScreen from 'expo-splash-screen';
import RootLayout from '../app/_layout';

jest.mock('expo-splash-screen', () => ({
  preventAutoHideAsync: jest.fn(() => Promise.resolve(true)),
  hideAsync: jest.fn(() => Promise.resolve()),
}));
jest.mock('../env', () => ({
  API_BASE_URL_VAR: 'EXPO_PUBLIC_API_BASE_URL',
  env: { ok: false, variable: 'EXPO_PUBLIC_API_BASE_URL', problem: 'missing' },
}));
jest.mock('../api', () => ({ api: null }));

const hide = jest.mocked(SplashScreen.hideAsync);

beforeEach(() => {
  hide.mockReset().mockImplementation(() => Promise.resolve());
});

describe('the root layout with a broken environment', () => {
  it('hides the splash when it renders the config error screen', async () => {
    await render(<RootLayout />);
    expect(screen.getByTestId('config-error-screen')).toBeTruthy();
    expect(hide).toHaveBeenCalledTimes(1);
  });

  it('still renders the config error screen when hideAsync rejects', async () => {
    hide.mockImplementation(() => Promise.reject(new Error('no native splash')));
    await render(<RootLayout />);
    expect(screen.getByTestId('config-error-screen')).toBeTruthy();
  });

  it('still renders the config error screen when hideAsync throws synchronously', async () => {
    hide.mockImplementation(() => {
      throw new Error('sync boom');
    });
    await render(<RootLayout />);
    expect(screen.getByTestId('config-error-screen')).toBeTruthy();
  });
});
