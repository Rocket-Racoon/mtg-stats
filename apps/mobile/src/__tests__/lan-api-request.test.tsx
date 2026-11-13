import { render, screen } from '@testing-library/react-native';
import { AppProviders } from '../components/AppProviders';
import { DevBuildInfo } from '../components/DevBuildInfo';

// AC1 (automatable part): EXPO_PUBLIC_API_BASE_URL set to the dev machine's LAN address.
const LAN = 'http://192.168.1.50:8000/api/v1';
jest.mock('../env', () => ({
  API_BASE_URL_VAR: 'EXPO_PUBLIC_API_BASE_URL',
  env: { ok: true, apiBaseUrl: 'http://192.168.1.50:8000/api/v1' },
}));

const READY = { status: 'ok', version: 'dev', checks: { database: 'ok', redis: 'ok' } };

describe('LAN API address (S-1.7.4 AC1)', () => {
  afterEach(() => jest.restoreAllMocks());

  it('requests exactly GET <lan>/api/v1/health/ready and renders the reachable line on 200', async () => {
    const fetchMock = jest.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(READY), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    );
    await render(
      <AppProviders>
        <DevBuildInfo />
      </AppProviders>,
    );
    expect(await screen.findByText('API: reachable (database ok, redis ok)')).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const request = fetchMock.mock.calls[0]?.[0] as Request;
    expect(request.url).toBe(`${LAN}/health/ready`);
    expect(request.method).toBe('GET');
    expect(screen.getByTestId('about-api-base-url')).toHaveTextContent(LAN);
  });
});
