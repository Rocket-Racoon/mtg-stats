import { router } from 'expo-router';
import * as lib from 'expo-router/testing-library';
import { BackHandler, StyleSheet } from 'react-native';
import { SessionProvider } from '../session/SessionProvider';
import { RootNavigator } from '../app/_layout';
import appJson from '../../app.json';

jest.setTimeout(60000);

const APP_DIR = './src/app';
const mount = (initialUrl: string) => lib.renderRouter(APP_DIR, { initialUrl });

async function signIn() {
  await lib.fireEvent.press(await lib.screen.findByLabelText('Continue (placeholder)'));
  await lib.screen.findByLabelText('Play');
}

/** Signs in with a protected link pending: the link's screen opens instead of the tabs. */
async function signInToPendingLink() {
  await lib.fireEvent.press(await lib.screen.findByLabelText('Continue (placeholder)'));
  await lib.screen.findByTestId('join-screen');
}

type BackHandlerFn = () => boolean | null | undefined;
let backHandlers: BackHandlerFn[] = [];
let exitApp: jest.SpyInstance;
let addListener: jest.SpyInstance;

beforeEach(() => {
  backHandlers = [];
  const original = BackHandler.addEventListener.bind(BackHandler);
  addListener = jest.spyOn(BackHandler, 'addEventListener').mockImplementation((name, handler) => {
    const fn = handler as BackHandlerFn;
    if (name !== 'hardwareBackPress') return original(name, handler);
    backHandlers.push(fn);
    const subscription = original(name, handler);
    return {
      remove: () => {
        backHandlers = backHandlers.filter((h) => h !== fn); // a removed listener must not fire
        subscription.remove();
      },
    };
  });
  exitApp = jest.spyOn(BackHandler, 'exitApp').mockImplementation(() => undefined);
});

afterEach(() => {
  addListener.mockRestore();
  exitApp.mockRestore();
});

/** Simulates Android's hardware back: handlers run newest first; the first to return true wins. */
async function pressHardwareBack() {
  await lib.act(async () => {
    for (const handler of [...backHandlers].reverse()) {
      if (handler()) return;
    }
    BackHandler.exitApp(); // nobody consumed it: Android leaves the app
  });
}

type StyleNode = { props: { style?: unknown }; parent: unknown } | null;

function minSize(node: StyleNode) {
  let n = node;
  while (n) {
    const style = StyleSheet.flatten(n.props.style as object) as
      { minHeight?: number; minWidth?: number } | undefined;
    if (style?.minHeight !== undefined) return style;
    n = n.parent as StyleNode;
  }
  return undefined;
}

describe('hardware back across tabs (firstRoute)', () => {
  it('goes from the Decks root to Play without leaving the app, then exits from Play', async () => {
    await mount('/');
    await signIn();
    await lib.fireEvent.press(lib.screen.getByLabelText('Decks'));
    await lib.screen.findByRole('header', { name: 'Decks' });
    await pressHardwareBack();
    expect(await lib.screen.findByRole('header', { name: 'MTG Stats' })).toBeTruthy();
    expect(exitApp).not.toHaveBeenCalled();
    await pressHardwareBack();
    expect(exitApp).toHaveBeenCalledTimes(1);
  });

  it.each(['Stats', 'More'])('goes from the %s root to Play', async (name) => {
    await mount('/');
    await signIn();
    await lib.fireEvent.press(lib.screen.getByLabelText(name));
    await lib.screen.findByRole('header', { name });
    await pressHardwareBack();
    expect(await lib.screen.findByRole('header', { name: 'MTG Stats' })).toBeTruthy();
    expect(exitApp).not.toHaveBeenCalled();
  });

  it('pops a screen inside the Play stack before switching tabs or exiting', async () => {
    await mount('/');
    await signIn();
    await lib.fireEvent.press(await lib.screen.findByLabelText('Open details'));
    await lib.screen.findByText('Play details');
    await pressHardwareBack();
    await lib.waitFor(() => expect(lib.screen.queryByText('Play details')).toBeNull());
    expect(exitApp).not.toHaveBeenCalled();
    await pressHardwareBack();
    expect(exitApp).toHaveBeenCalledTimes(1);
  });

  it('drops the deep-link listener once the join screen opened via the pending link is left', async () => {
    await mount('/join/ABC123');
    await signInToPendingLink(); // join is protected: the pending link opens it after sign-in
    await lib.fireEvent.press(await lib.screen.findByLabelText('Back to app'));
    await lib.waitFor(() => expect(lib.screen.queryByTestId('join-screen')).toBeNull());
    await lib.screen.findByLabelText('Play');
    await pressHardwareBack();
    expect(exitApp).toHaveBeenCalledTimes(1);
  });
});

describe('opening the app already signed in', () => {
  it('shows the tabs straight away when the session starts signed in', async () => {
    await lib.renderRouter(
      {
        appDir: APP_DIR,
        overrides: {
          _layout: () => (
            <SessionProvider initialStatus="signed_in">
              <RootNavigator />
            </SessionProvider>
          ),
        },
      },
      { initialUrl: '/' },
    );
    expect(await lib.screen.findByLabelText('Play')).toBeTruthy();
    expect(lib.screen.queryByLabelText('Continue (placeholder)')).toBeNull();
  });
});

describe('cold start signed in on a join link (no history)', () => {
  async function mountSignedInAt(url: string) {
    await lib.renderRouter(
      {
        appDir: APP_DIR,
        overrides: {
          _layout: () => (
            <SessionProvider initialStatus="signed_in">
              <RootNavigator />
            </SessionProvider>
          ),
        },
      },
      { initialUrl: url },
    );
    await lib.screen.findByTestId('join-screen');
    expect(router.canGoBack()).toBe(false);
  }

  it('hardware back goes to the Play tab instead of exiting the app', async () => {
    await mountSignedInAt('/join/ABC123');
    await pressHardwareBack();
    expect(await lib.screen.findByLabelText('Play')).toBeTruthy();
    expect(lib.screen.queryByTestId('join-screen')).toBeNull();
    expect(exitApp).not.toHaveBeenCalled();
  });

  it('Back to app goes to the Play tab', async () => {
    await mountSignedInAt('/join/ABC123');
    await lib.fireEvent.press(lib.screen.getByLabelText('Back to app'));
    expect(await lib.screen.findByLabelText('Play')).toBeTruthy();
    expect(lib.screen.queryByTestId('join-screen')).toBeNull();
  });
});

describe('app configuration', () => {
  it('registers the mtgstats URL scheme the deep links depend on', () => {
    expect(appJson.expo.scheme).toBe('mtgstats');
  });
});

describe('guards', () => {
  it('lands a signed-out user on sign-in when opening /decks', async () => {
    await mount('/decks');
    expect(await lib.screen.findByLabelText('Continue (placeholder)')).toBeTruthy();
    expect(lib.screen.queryByLabelText('Decks')).toBeNull();
  });

  it('does not let a signed-in user reach sign-in by URL', async () => {
    await mount('/');
    await signIn();
    await lib.act(async () => router.replace('/sign-in'));
    expect(lib.screen.queryByLabelText('Continue (placeholder)')).toBeNull();
    expect(lib.screen.getByLabelText('Play')).toBeTruthy();
  });

  it('makes the tabs unreachable again after sign out', async () => {
    await mount('/');
    await signIn();
    await lib.fireEvent.press(lib.screen.getByLabelText('More'));
    await lib.fireEvent.press(await lib.screen.findByLabelText('Sign out'));
    await lib.screen.findByLabelText('Continue (placeholder)');
    await lib.act(async () => router.replace('/decks'));
    expect(lib.screen.queryByLabelText('Decks')).toBeNull();
    expect(lib.screen.getByLabelText('Continue (placeholder)')).toBeTruthy();
  });
});

describe('tabs', () => {
  it('opens each of the four tabs with its own screen when its button is pressed', async () => {
    await mount('/');
    await signIn();
    for (const name of ['Decks', 'Stats', 'More', 'Play']) {
      await lib.fireEvent.press(lib.screen.getByLabelText(name));
      const header = name === 'Play' ? 'MTG Stats' : name;
      expect(await lib.screen.findByRole('header', { name: header })).toBeTruthy();
    }
  });

  it('shows Decks and Stats as placeholders for features not built yet', async () => {
    await mount('/');
    await signIn();
    for (const name of ['Decks', 'Stats']) {
      await lib.fireEvent.press(lib.screen.getByLabelText(name));
      await lib.screen.findByRole('header', { name });
      expect(lib.screen.getByText(/coming soon/i)).toBeTruthy();
    }
  });
});

describe('hardware back (Android)', () => {
  it('pops a pushed screen inside the Play stack instead of leaving the app', async () => {
    await mount('/');
    await signIn();
    await lib.fireEvent.press(await lib.screen.findByLabelText('Open details'));
    await lib.screen.findByText('Play details');
    await pressHardwareBack();
    await lib.waitFor(() => expect(lib.screen.queryByText('Play details')).toBeNull());
    expect(lib.screen.getByLabelText('Open details')).toBeTruthy();
    expect(exitApp).not.toHaveBeenCalled();
  });

  it('exits the app from a root tab', async () => {
    await mount('/');
    await signIn();
    await pressHardwareBack();
    expect(exitApp).toHaveBeenCalledTimes(1);
  });

  it('pops a join screen pushed over a tab and returns to that tab', async () => {
    await mount('/');
    await signIn();
    await lib.fireEvent.press(lib.screen.getByLabelText('Stats'));
    await lib.screen.findByRole('header', { name: 'Stats' });
    await lib.act(async () => router.push('/join/XYZ'));
    await lib.screen.findByTestId('join-screen');
    await pressHardwareBack();
    await lib.waitFor(() => expect(lib.screen.queryByTestId('join-screen')).toBeNull());
    expect(lib.screen.getByRole('header', { name: 'Stats' })).toBeTruthy();
  });

  it('hardware back on a join screen opened via the pending link returns to the tabs', async () => {
    await mount('/join/ABC123');
    await signInToPendingLink(); // join is protected: the pending link opens it after sign-in
    await lib.screen.findByTestId('join-screen');
    await pressHardwareBack();
    expect(await lib.screen.findByLabelText('Play')).toBeTruthy();
    expect(lib.screen.queryByTestId('join-screen')).toBeNull();
    expect(exitApp).not.toHaveBeenCalled();
  });

  it('goes to the app root on a cold-start invite screen', async () => {
    await mount('/invite/tok-1');
    await lib.screen.findByTestId('invite-screen');
    await pressHardwareBack();
    expect(await lib.screen.findByLabelText('Continue (placeholder)')).toBeTruthy();
    expect(lib.screen.queryByTestId('invite-screen')).toBeNull();
  });
});

describe('deep links', () => {
  it('opens the join screen while signed in, over the tabs, and back returns to them', async () => {
    await mount('/');
    await signIn();
    await lib.act(async () => router.push('/join/ROOM42'));
    expect(await lib.screen.findByTestId('join-screen-value')).toHaveTextContent('ROOM42');
    await lib.fireEvent.press(lib.screen.getByLabelText('Back to app'));
    await lib.waitFor(() => expect(lib.screen.queryByTestId('join-screen')).toBeNull());
    expect(lib.screen.getByLabelText('Play')).toBeTruthy();
  });

  it('opens the invite screen while signed out and Back to app shows sign-in', async () => {
    await mount('/invite/tok-signed-out');
    expect(await lib.screen.findByTestId('invite-screen-value')).toHaveTextContent(
      'tok-signed-out',
    );
    await lib.fireEvent.press(lib.screen.getByLabelText('Back to app'));
    expect(await lib.screen.findByLabelText('Continue (placeholder)')).toBeTruthy();
  });

  it('renders a URL-encoded code decoded', async () => {
    await mount('/join/AB%20C%2F1');
    await signInToPendingLink(); // join is protected: the pending link opens it after sign-in
    expect(await lib.screen.findByTestId('join-screen-value')).toHaveTextContent('AB C/1');
  });

  it('renders a token containing markup characters as plain text', async () => {
    await mount('/invite/%3Cb%3Ex%3C%2Fb%3E');
    expect(await lib.screen.findByTestId('invite-screen-value')).toHaveTextContent('<b>x</b>');
  });
});

describe('accessibility', () => {
  it('labels every button and gives it a 44 pt minimum target', async () => {
    await mount('/');
    await signIn();
    const { screen } = lib;
    const check = (label: string) => {
      const b = screen.getByLabelText(label);
      expect(b.props.accessibilityRole).toBe('button');
      const s = StyleSheet.flatten(b.props.style);
      expect(s.minHeight).toBeGreaterThanOrEqual(44);
      expect(s.minWidth).toBeGreaterThanOrEqual(44);
    };
    check('Open details');
    await lib.fireEvent.press(screen.getByLabelText('More'));
    await screen.findByLabelText('Sign out');
    check('Sign out');
    await lib.act(async () => router.push('/join/ABC'));
    await screen.findByLabelText('Back to app');
    check('Back to app');
  });

  it('gives every tab a label and a 44 pt target on the signed-in shell', async () => {
    await mount('/');
    await signIn();
    for (const name of ['Play', 'Decks', 'Stats', 'More']) {
      const node = lib.screen.getByTestId(`tab-${name.toLowerCase()}`);
      expect(lib.screen.getByLabelText(name)).toBeTruthy();
      const size = minSize(node as never);
      expect(size?.minHeight).toBeGreaterThanOrEqual(44);
      expect(size?.minWidth).toBeGreaterThanOrEqual(44);
    }
  });
});
