import * as lib from 'expo-router/testing-library';

jest.setTimeout(60000);

jest.mock('expo-constants', () => ({
  __esModule: true,
  default: { expoConfig: { version: '1.2.3', extra: { buildProfile: 'development' } } },
}));
// eslint-disable-next-line @typescript-eslint/no-require-imports
const mockConstants = require('expo-constants').default as {
  expoConfig: { extra: { buildProfile: string } };
};

const APP_DIR = './src/app';
const URL_TEXT = 'http://localhost:8000/api/v1';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

const setDev = (value: boolean) => {
  (globalThis as unknown as { __DEV__: boolean }).__DEV__ = value;
};

describe('About dev info (S-1.7.4 AC3)', () => {
  afterEach(() => {
    jest.restoreAllMocks();
    setDev(true);
    mockConstants.expoConfig.extra.buildProfile = 'development';
  });

  it('shows the profile, the API URL and the readiness result in a dev build', async () => {
    jest
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(
        json({ status: 'ok', version: 'dev', checks: { database: 'ok', redis: 'ok' } }),
      );
    await lib.renderRouter(APP_DIR, { initialUrl: '/about' });
    const { screen } = lib;
    expect(await screen.findByTestId('dev-build-info')).toBeTruthy();
    expect(screen.getByTestId('build-profile')).toHaveTextContent('development');
    expect(screen.getByTestId('about-api-base-url')).toHaveTextContent(URL_TEXT);
    expect(await screen.findByText('API: reachable (database ok, redis ok)')).toBeTruthy();
  });

  it('names the failed check when the API answers 503 and retries on demand', async () => {
    const fetchMock = jest
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(
        json({ status: 'error', version: 'dev', checks: { database: 'ok', redis: 'error' } }, 503),
      )
      .mockResolvedValue(
        json({ status: 'ok', version: 'dev', checks: { database: 'ok', redis: 'ok' } }),
      );
    await lib.renderRouter(APP_DIR, { initialUrl: '/about' });
    const { screen, fireEvent } = lib;
    expect(await screen.findByText('API not ready (database ok, redis error)')).toBeTruthy();
    await fireEvent.press(screen.getByLabelText('Check API again'));
    expect(await screen.findByText('API: reachable (database ok, redis ok)')).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('says the API is unreachable on a network failure', async () => {
    jest.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('Network request failed'));
    await lib.renderRouter(APP_DIR, { initialUrl: '/about' });
    expect(await lib.screen.findByText('API unreachable (network error)')).toBeTruthy();
  });

  it('hides everything in a release bundle (__DEV__ false) and makes no API call', async () => {
    setDev(false);
    const fetchMock = jest.spyOn(globalThis, 'fetch');
    await lib.renderRouter(APP_DIR, { initialUrl: '/about' });
    const { screen } = lib;
    expect(await screen.findByRole('header', { name: 'About' })).toBeTruthy();
    expect(screen.queryByTestId('dev-build-info')).toBeNull();
    expect(screen.queryByText(new RegExp(URL_TEXT))).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('hides the API URL when the profile is production even if __DEV__ is true', async () => {
    mockConstants.expoConfig.extra.buildProfile = 'production';
    const fetchMock = jest.spyOn(globalThis, 'fetch');
    await lib.renderRouter(APP_DIR, { initialUrl: '/about' });
    expect(await lib.screen.findByRole('header', { name: 'About' })).toBeTruthy();
    expect(lib.screen.queryByTestId('about-api-base-url')).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
