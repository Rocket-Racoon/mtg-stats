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
const READY = { status: 'ok', version: 'dev', checks: { database: 'ok', redis: 'ok' } };

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

const setDev = (value: boolean) => {
  (globalThis as unknown as { __DEV__: boolean }).__DEV__ = value;
};

describe('About dev info matrix (S-1.7.4 AC3)', () => {
  afterEach(() => {
    jest.restoreAllMocks();
    setDev(true);
    mockConstants.expoConfig.extra.buildProfile = 'development';
  });

  const matrix: [boolean, string][] = [
    [true, 'development'],
    [true, 'preview'],
    [true, 'production'],
    [false, 'development'],
    [false, 'preview'],
    [false, 'production'],
  ];

  it.each(matrix)('__DEV__=%s, profile=%s', async (dev, profile) => {
    setDev(dev);
    mockConstants.expoConfig.extra.buildProfile = profile;
    const fetchMock = jest.spyOn(globalThis, 'fetch').mockResolvedValue(json(READY));
    await lib.renderRouter(APP_DIR, { initialUrl: '/about' });
    const { screen } = lib;
    expect(await screen.findByRole('header', { name: 'About' })).toBeTruthy();
    const shown = dev && profile !== 'production';
    if (shown) {
      expect(await screen.findByTestId('about-api-base-url')).toHaveTextContent(URL_TEXT);
      // The profile shown equals extra.buildProfile.
      expect(screen.getByTestId('build-profile')).toHaveTextContent(profile);
      expect(await screen.findByText('API: reachable (database ok, redis ok)')).toBeTruthy();
      expect(fetchMock).toHaveBeenCalled();
    } else {
      expect(screen.queryByTestId('about-api-base-url')).toBeNull();
      expect(screen.queryByTestId('dev-build-info')).toBeNull();
      // No API URL text anywhere in the rendered tree.
      expect(JSON.stringify(screen.toJSON())).not.toContain('localhost:8000');
      expect(JSON.stringify(screen.toJSON())).not.toContain('/api/v1');
      expect(fetchMock).not.toHaveBeenCalled();
    }
  });

  it('shows the error code when the API answers with an error envelope', async () => {
    jest
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(
        json(
          { error: { code: 'not_found', message: 'nope', details: null, request_id: 'r1' } },
          404,
        ),
      );
    await lib.renderRouter(APP_DIR, { initialUrl: '/about' });
    expect(await lib.screen.findByText('API error (not_found)')).toBeTruthy();
  });

  it('refetches when "Check API again" is pressed after an error response', async () => {
    const fetchMock = jest
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(
        json(
          { error: { code: 'not_found', message: 'nope', details: null, request_id: 'r1' } },
          404,
        ),
      )
      .mockResolvedValue(json(READY));
    await lib.renderRouter(APP_DIR, { initialUrl: '/about' });
    const { screen, fireEvent } = lib;
    expect(await screen.findByText('API error (not_found)')).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await fireEvent.press(screen.getByLabelText('Check API again'));
    expect(await screen.findByText('API: reachable (database ok, redis ok)')).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
