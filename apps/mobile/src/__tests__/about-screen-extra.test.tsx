import {
  FAN_CONTENT_DISCLAIMER,
  FAN_CONTENT_DISCLAIMER_TEXT,
  FAN_CONTENT_POLICY_URL,
  SCRYFALL_CREDIT,
  SCRYFALL_URL,
} from '@mtg-stats/card-utils';
import * as lib from 'expo-router/testing-library';
import fs from 'node:fs';
import path from 'node:path';
import { BackHandler, Linking, StyleSheet } from 'react-native';
import { AboutScreen } from '../components/AboutScreen';
import { ExternalLink } from '../components/ExternalLink';
import AuthAbout from '../app/(auth)/about';
import MoreAbout from '../app/(tabs)/more/about';

jest.setTimeout(60000);

jest.mock('expo-constants', () => ({
  __esModule: true,
  default: { expoConfig: { version: '4.5.6' } },
}));
// eslint-disable-next-line @typescript-eslint/no-require-imports
const mockConstants = require('expo-constants').default as {
  expoConfig: { version?: string } | null;
};

/** Mounts AboutScreen inside an in-memory route tree (it uses Stack.Screen). */
const renderAbout = () => lib.renderRouter({ index: AboutScreen });

const APP_DIR = './src/app';

type BackHandlerFn = () => boolean | null | undefined;
let backHandlers: BackHandlerFn[] = [];
let exitApp: jest.SpyInstance;
let addListener: jest.SpyInstance;

beforeEach(() => {
  mockConstants.expoConfig = { version: '4.5.6' };
  backHandlers = [];
  const original = BackHandler.addEventListener.bind(BackHandler);
  addListener = jest.spyOn(BackHandler, 'addEventListener').mockImplementation((name, handler) => {
    const fn = handler as BackHandlerFn;
    if (name !== 'hardwareBackPress') return original(name, handler);
    backHandlers.push(fn);
    const subscription = original(name, handler);
    return {
      remove: () => {
        backHandlers = backHandlers.filter((h) => h !== fn);
        subscription.remove();
      },
    };
  });
  exitApp = jest.spyOn(BackHandler, 'exitApp').mockImplementation(() => undefined);
});

afterEach(() => {
  addListener.mockRestore();
  exitApp.mockRestore();
  jest.restoreAllMocks();
});

/** Simulates Android hardware back: newest handler first; unconsumed back exits the app. */
async function pressHardwareBack() {
  await lib.act(async () => {
    for (const handler of [...backHandlers].reverse()) {
      if (handler()) return;
    }
    BackHandler.exitApp();
  });
}

type StyleNode = { props: { style?: unknown }; parent: unknown } | null;
function minSize(node: unknown) {
  let n = node as StyleNode;
  while (n) {
    const style = StyleSheet.flatten(n.props.style as object) as
      { minHeight?: number; minWidth?: number } | undefined;
    if (style?.minHeight !== undefined) return style;
    n = n.parent as StyleNode;
  }
  return undefined;
}

describe('AC1: version and title', () => {
  it('shows the version from the Expo config', async () => {
    await renderAbout();
    expect(lib.screen.getByTestId('app-version')).toHaveTextContent('4.5.6', { exact: true });
  });

  it('falls back to "unknown" when the Expo config has no version', async () => {
    mockConstants.expoConfig = null;
    await renderAbout();
    expect(lib.screen.getByTestId('app-version')).toHaveTextContent('unknown', { exact: true });
  });

  it('exposes the screen title "About" as a header', async () => {
    await renderAbout();
    expect(lib.screen.getByRole('header', { name: 'About' })).toBeTruthy();
  });
});

describe('AC1: wording is the shared module, in the web footer order', () => {
  /** Composes plain text the way apps/web/src/Attribution.tsx does: segment text or link label. */
  const webPlain = (segments: typeof FAN_CONTENT_DISCLAIMER) =>
    segments.map((s) => (s.kind === 'text' ? s.text : s.label)).join('');

  it('renders the full disclaimer exactly as the web composes it', async () => {
    await renderAbout();
    const text = webPlain(FAN_CONTENT_DISCLAIMER);
    expect(text).toBe(FAN_CONTENT_DISCLAIMER_TEXT);
    expect(lib.screen.getByTestId('fan-content-notice')).toHaveTextContent(text, { exact: true });
  });

  it('renders "Card data and images courtesy of Scryfall" as the web composes it', async () => {
    await renderAbout();
    expect(webPlain(SCRYFALL_CREDIT)).toBe('Card data and images courtesy of Scryfall');
    expect(lib.screen.getByTestId('scryfall-credit')).toHaveTextContent(
      'Card data and images courtesy of Scryfall',
      { exact: true },
    );
  });

  it('shows the disclaimer before the Scryfall credit, as the web footer does', async () => {
    await renderAbout();
    const ids = lib.screen
      .getAllByTestId(/^(fan-content-notice|scryfall-credit)$/)
      .map((n) => n.props.testID);
    expect(ids).toEqual(['fan-content-notice', 'scryfall-credit']);
  });

  it('labels the two links with the shared link labels, in order', async () => {
    expect(new URL(SCRYFALL_URL).host).toBe('scryfall.com');
    expect(FAN_CONTENT_POLICY_URL.startsWith('https://')).toBe(true);
    await renderAbout();
    expect(lib.screen.getAllByRole('link').map((l) => l.props.accessibilityLabel)).toEqual([
      'Fan Content Policy',
      'Scryfall',
    ]);
  });
});

describe('AC2: links', () => {
  it('opens each link with exactly its shared URL', async () => {
    const open = jest.spyOn(Linking, 'openURL').mockResolvedValue();
    await renderAbout();
    await lib.fireEvent.press(lib.screen.getByRole('link', { name: 'Fan Content Policy' }));
    await lib.fireEvent.press(lib.screen.getByRole('link', { name: 'Scryfall' }));
    expect(open.mock.calls).toEqual([[FAN_CONTENT_POLICY_URL], [SCRYFALL_URL]]);
  });

  it('does not crash when openURL rejects', async () => {
    jest.spyOn(Linking, 'openURL').mockRejectedValue(new Error('no browser'));
    await renderAbout();
    await lib.fireEvent.press(lib.screen.getByRole('link', { name: 'Scryfall' }));
    await lib.act(async () => undefined);
    expect(lib.screen.getByRole('header', { name: 'About' })).toBeTruthy();
  });

  it('gives a standalone external link a 44 pt minimum target and a link role', async () => {
    jest.spyOn(Linking, 'openURL').mockResolvedValue();
    await lib.renderRouter({ index: () => <ExternalLink label="Scryfall" href={SCRYFALL_URL} /> });
    const size = minSize(lib.screen.getByRole('link', { name: 'Scryfall' }));
    expect(size?.minHeight).toBeGreaterThanOrEqual(44);
    expect(size?.minWidth).toBeGreaterThanOrEqual(44);
  });
});

describe('AC3: one About screen, reachable signed out', () => {
  it('mounts the same AboutScreen component from both route files', () => {
    expect(AuthAbout).toBe(AboutScreen);
    expect(MoreAbout).toBe(AboutScreen);
  });

  it('shows an About button on sign-in with a 44 pt target', async () => {
    await lib.renderRouter(APP_DIR, { initialUrl: '/' });
    const about = await lib.screen.findByLabelText('About');
    expect(minSize(about)?.minHeight).toBeGreaterThanOrEqual(44);
  });

  it('keeps the tabs unreachable from About while signed out', async () => {
    await lib.renderRouter(APP_DIR, { initialUrl: '/' });
    await lib.fireEvent.press(await lib.screen.findByLabelText('About'));
    await lib.screen.findByRole('header', { name: 'About' });
    expect(lib.screen).toHavePathname('/about');
    expect(lib.screen.queryByLabelText('Play')).toBeNull();
    expect(lib.screen.queryByLabelText('More')).toBeNull();
  });

  it('returns to sign-in on Android back from About', async () => {
    await lib.renderRouter(APP_DIR, { initialUrl: '/' });
    await lib.fireEvent.press(await lib.screen.findByLabelText('About'));
    await lib.screen.findByRole('header', { name: 'About' });
    await pressHardwareBack();
    expect(await lib.screen.findByLabelText('Continue (placeholder)')).toBeTruthy();
    expect(exitApp).not.toHaveBeenCalled();
  });

  it('opens About directly on a cold start at /about while signed out', async () => {
    await lib.renderRouter(APP_DIR, { initialUrl: '/about' });
    expect(await lib.screen.findByRole('header', { name: 'About' })).toBeTruthy();
    expect(lib.screen.queryByLabelText('Play')).toBeNull();
  });

  it('returns to the More tab on Android back from About when signed in', async () => {
    await lib.renderRouter(APP_DIR, { initialUrl: '/' });
    await lib.fireEvent.press(await lib.screen.findByLabelText('Continue (placeholder)'));
    await lib.fireEvent.press(await lib.screen.findByLabelText('More'));
    const about = await lib.screen.findByLabelText('About');
    expect(minSize(about)?.minHeight).toBeGreaterThanOrEqual(44);
    await lib.fireEvent.press(about);
    await lib.screen.findByRole('header', { name: 'About' });
    await pressHardwareBack();
    expect(await lib.screen.findByLabelText('Sign out')).toBeTruthy();
    expect(exitApp).not.toHaveBeenCalled();
  });
});

describe('no copied notice strings in mobile source', () => {
  function sourceFiles(dir: string): string[] {
    return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) return e.name === '__tests__' ? [] : sourceFiles(p);
      return /\.(tsx?|jsx?)$/.test(e.name) && !/\.test\./.test(e.name) ? [p] : [];
    });
  }

  it('contains no disclaimer or credit literal outside tests', () => {
    const files = sourceFiles(path.resolve(__dirname, '..'));
    expect(files.length).toBeGreaterThan(5);
    const needles = [
      'unofficial Fan Content',
      'Not approved',
      'Portions of the materials',
      'Wizards of the Coast',
      'courtesy of',
      'Card data and images',
      'fancontentpolicy',
      'scryfall.com',
    ];
    const hits = files.flatMap((f) => {
      const src = fs.readFileSync(f, 'utf8').toLowerCase();
      return needles.filter((n) => src.includes(n.toLowerCase())).map((n) => `${f}: ${n}`);
    });
    expect(hits).toEqual([]);
  });
});
