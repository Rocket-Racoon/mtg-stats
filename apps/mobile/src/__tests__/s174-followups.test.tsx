import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import * as lib from 'expo-router/testing-library';
import { loadRootEnv } from '../../app.config';

jest.setTimeout(60000);

jest.mock('expo-constants', () => ({
  __esModule: true,
  default: { expoConfig: { version: '1.0.0', extra: { buildProfile: 'development' } } },
}));

const APP_DIR = './src/app';

describe('loadRootEnv is for the development profile only', () => {
  let dir: string;
  beforeEach(() => {
    dir = mkdtempSync(path.join(tmpdir(), 'root-env-profile-'));
    writeFileSync(
      path.join(dir, '.env'),
      'EXPO_PUBLIC_API_BASE_URL=http://192.168.1.20:8000/api/v1\n',
    );
  });
  afterEach(() => rmSync(dir, { recursive: true, force: true }));

  it.each(['preview', 'production'])('does not read the root .env for %s', (profile) => {
    const env: Record<string, string | undefined> = { EAS_BUILD_PROFILE: profile };
    loadRootEnv(env, dir);
    expect(env.EXPO_PUBLIC_API_BASE_URL).toBeUndefined();
  });

  it('reads the root .env for development', () => {
    const env: Record<string, string | undefined> = { APP_PROFILE: 'development' };
    loadRootEnv(env, dir);
    expect(env.EXPO_PUBLIC_API_BASE_URL).toBe('http://192.168.1.20:8000/api/v1');
  });
});

describe('DevBuildInfo wording', () => {
  afterEach(() => jest.restoreAllMocks());

  it('does not blame the database when another server answers 404 HTML', async () => {
    jest.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('<html>Not Found</html>', {
        status: 404,
        headers: { 'content-type': 'text/html' },
      }),
    );
    await lib.renderRouter(APP_DIR, { initialUrl: '/about' });
    expect(await lib.screen.findByText('API error (HTTP 404, unexpected response)')).toBeTruthy();
    expect(lib.screen.queryByText(/database/)).toBeNull();
  });

  it('shows "checking" while a retry is in flight instead of the old error', async () => {
    let release: (r: Response) => void = () => undefined;
    jest
      .spyOn(globalThis, 'fetch')
      .mockRejectedValueOnce(new TypeError('Network request failed'))
      .mockImplementationOnce(() => new Promise<Response>((resolve) => (release = resolve)));
    await lib.renderRouter(APP_DIR, { initialUrl: '/about' });
    const { screen, fireEvent } = lib;
    expect(await screen.findByText('API unreachable (network error)')).toBeTruthy();
    await fireEvent.press(screen.getByLabelText('Check API again'));
    expect(await screen.findByText('API: checking...')).toBeTruthy();
    expect(screen.queryByText('API unreachable (network error)')).toBeNull();
    release(
      new Response(
        JSON.stringify({ status: 'ok', version: 'dev', checks: { database: 'ok', redis: 'ok' } }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      ),
    );
    expect(await screen.findByText('API: reachable (database ok, redis ok)')).toBeTruthy();
  });
});
