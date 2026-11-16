import * as lib from 'expo-router/testing-library';
import * as SplashScreen from 'expo-splash-screen';

jest.setTimeout(60000);

jest.mock('expo-splash-screen', () => ({
  preventAutoHideAsync: jest.fn(() => Promise.resolve(true)),
  hideAsync: jest.fn(() => Promise.resolve()),
}));
jest.mock('../env', () => ({
  API_BASE_URL_VAR: 'EXPO_PUBLIC_API_BASE_URL',
  env: { ok: false, variable: 'EXPO_PUBLIC_API_BASE_URL', problem: 'missing' },
}));
jest.mock('../api', () => ({ api: null }));

describe('root layout with an invalid environment (splash)', () => {
  it('shows the config error screen and hides the splash so it is never covered', async () => {
    await lib.renderRouter('./src/app', { initialUrl: '/' });
    expect(await lib.screen.findByTestId('config-error-screen')).toBeTruthy();
    expect(lib.screen.queryByLabelText('Continue (placeholder)')).toBeNull();
    expect(lib.screen.queryByLabelText('Play')).toBeNull();
    expect(SplashScreen.hideAsync).toHaveBeenCalled();
  });
});
