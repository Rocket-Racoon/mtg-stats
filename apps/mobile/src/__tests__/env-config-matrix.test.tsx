/* eslint-disable @typescript-eslint/no-require-imports */
import type { ReactNode } from 'react';

jest.setTimeout(60000);

const VAR = 'EXPO_PUBLIC_API_BASE_URL';
// RNTL auto cleanup is skipped here, so unmount explicitly (frees TanStack's gc timers).
let lastRtl: typeof import('@testing-library/react-native') | undefined;
afterEach(() => lastRtl?.cleanup());
/** Loads the app modules fresh with the variable set to `value` (env.ts reads it at import). */
function loadWithEnv(value: string | undefined) {
  const saved = process.env[VAR];
  process.env.RNTL_SKIP_AUTO_CLEANUP = 'true'; // RNTL registers hooks at import; not allowed inside a test
  if (value === undefined) delete process.env[VAR];
  else process.env[VAR] = value;
  const loaded = {} as {
    rtl: typeof import('@testing-library/react-native');
    AppProviders: (props: { children: ReactNode }) => ReactNode;
    RootLayout: () => ReactNode;
    Text: typeof import('react-native').Text;
  };
  try {
    jest.isolateModules(() => {
      loaded.rtl = require('@testing-library/react-native');
      loaded.AppProviders = require('../components/AppProviders').AppProviders;
      loaded.RootLayout = require('../app/_layout').default;
      loaded.Text = require('react-native').Text;
    });
  } finally {
    if (saved === undefined) delete process.env[VAR];
    else process.env[VAR] = saved;
  }
  lastRtl = loaded.rtl;
  return loaded;
}

describe('AC2: a missing or invalid variable shows the configuration error screen', () => {
  afterEach(() => jest.restoreAllMocks());

  const bad: [string, string | undefined][] = [
    ['missing', undefined],
    ['empty', ''],
    ['whitespace only', '   '],
    ['ftp scheme', 'ftp://192.168.1.50/api/v1'],
    ['no scheme', 'localhost:8000'],
    ['garbage', 'not a url at all'],
  ];

  it.each(bad)('%s: shows the screen, with no children and no fetch', async (_name, value) => {
    const fetchMock = jest.spyOn(globalThis, 'fetch');
    const { rtl, AppProviders, Text } = loadWithEnv(value);
    await rtl.render(
      <AppProviders>
        <Text testID="child">app</Text>
      </AppProviders>,
    );
    const { screen } = rtl;
    expect(screen.getByTestId('config-error-screen')).toBeTruthy();
    expect(screen.getByRole('header', { name: 'Configuration error' })).toBeTruthy();
    expect(screen.getByTestId('config-error-variable')).toHaveTextContent(VAR);
    expect(screen.queryByTestId('child')).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(bad)('%s: the root layout renders no navigator or tabs', async (_name, value) => {
    const fetchMock = jest.spyOn(globalThis, 'fetch');
    const { rtl, RootLayout } = loadWithEnv(value);
    await rtl.render(<RootLayout />);
    const { screen } = rtl;
    expect(screen.getByTestId('config-error-screen')).toBeTruthy();
    expect(screen.getByTestId('config-error-variable')).toHaveTextContent(VAR);
    expect(screen.queryByRole('tab')).toBeNull();
    expect(screen.queryByRole('tablist')).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('a valid value renders the app children and no error screen', async () => {
    const { rtl, AppProviders, Text } = loadWithEnv('http://192.168.1.50:8000/api/v1');
    await rtl.render(
      <AppProviders>
        <Text testID="child">app</Text>
      </AppProviders>,
    );
    expect(rtl.screen.getByTestId('child')).toBeTruthy();
    expect(rtl.screen.queryByTestId('config-error-screen')).toBeNull();
  });
});
