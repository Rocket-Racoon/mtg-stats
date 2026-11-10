import { router } from 'expo-router';
import * as lib from 'expo-router/testing-library';
import { StyleSheet } from 'react-native';

jest.setTimeout(60000); // the first render loads the whole route tree and can be slow

// The real route tree under src/app, so the tests cover the actual layouts and guards.
const APP_DIR = './src/app';

async function mount(initialUrl: string) {
  return await lib.renderRouter(APP_DIR, { initialUrl });
}

async function signIn() {
  const { screen, fireEvent } = lib;
  await fireEvent.press(await screen.findByLabelText('Continue (placeholder)'));
  await screen.findByLabelText('Play tab');
}

describe('signed-out navigation', () => {
  it('shows the sign-in stack and no tabs at the root URL', async () => {
    await mount('/');
    const { screen } = lib;
    expect(await screen.findByLabelText('Continue (placeholder)')).toBeTruthy();
    expect(screen.queryByLabelText('Play tab')).toBeNull();
  });

  it('redirects a signed-out user away from a tab route', async () => {
    await mount('/play');
    const { screen } = lib;
    expect(await screen.findByLabelText('Continue (placeholder)')).toBeTruthy();
    expect(screen.queryByLabelText('Play tab')).toBeNull();
  });
});

describe('signed-in navigation', () => {
  it('shows the Play, Decks, Stats and More tabs after sign-in', async () => {
    await mount('/');
    await signIn();
    const { screen } = lib;
    for (const name of ['Play', 'Decks', 'Stats', 'More']) {
      expect(screen.getByLabelText(`${name} tab`)).toBeTruthy();
    }
    expect(screen.queryByLabelText('Continue (placeholder)')).toBeNull();
  });

  it('switches tabs when a tab is pressed', async () => {
    await mount('/');
    await signIn();
    const { screen, fireEvent } = lib;
    await fireEvent.press(screen.getByLabelText('Decks tab'));
    expect(await screen.findByText('Coming soon.')).toBeTruthy();
    expect(screen.getByRole('header', { name: 'Decks' })).toBeTruthy();
    await fireEvent.press(screen.getByLabelText('Stats tab'));
    expect(await screen.findByRole('header', { name: 'Stats' })).toBeTruthy();
  });

  it('returns to the sign-in stack after sign out', async () => {
    await mount('/');
    await signIn();
    const { screen, fireEvent } = lib;
    await fireEvent.press(screen.getByLabelText('More tab'));
    await fireEvent.press(await screen.findByLabelText('Sign out'));
    expect(await screen.findByLabelText('Continue (placeholder)')).toBeTruthy();
    expect(screen.queryByLabelText('Play tab')).toBeNull();
  });
});

describe('back navigation', () => {
  it('goes back one screen inside a tab stack', async () => {
    await mount('/');
    await signIn();
    const { screen, fireEvent, waitFor } = lib;
    expect(router.canGoBack()).toBe(false); // a root tab: nothing to pop, so Android exits the app
    await fireEvent.press(await screen.findByLabelText('Open details'));
    expect(await screen.findByText('Play details')).toBeTruthy();
    expect(router.canGoBack()).toBe(true);
    await lib.act(async () => router.back());
    await waitFor(() => expect(screen.queryByText('Play details')).toBeNull());
    expect(screen.getByLabelText('Open details')).toBeTruthy();
    expect(router.canGoBack()).toBe(false);
  });

  it('has nothing to go back to on every root tab', async () => {
    await mount('/');
    await signIn();
    const { screen, fireEvent } = lib;
    for (const name of ['Decks', 'Stats', 'More', 'Play']) {
      await fireEvent.press(screen.getByLabelText(`${name} tab`));
      await screen.findByRole('header', { name: name === 'Play' ? 'MTG Stats' : name });
      expect(router.canGoBack()).toBe(false);
    }
  });
});

describe('deep links', () => {
  it('opens the Join placeholder with the code from mtgstats://join/<code>', async () => {
    await mount('/join/ABC123');
    const { screen } = lib;
    expect(await screen.findByTestId('join-screen')).toBeTruthy();
    expect(screen.getByTestId('join-screen-value')).toHaveTextContent('ABC123');
  });

  it('opens the Invite placeholder with the token from mtgstats://invite/<token>', async () => {
    await mount('/invite/tok-9f2');
    const { screen } = lib;
    expect(await screen.findByTestId('invite-screen')).toBeTruthy();
    expect(screen.getByTestId('invite-screen-value')).toHaveTextContent('tok-9f2');
  });

  it('on a cold start, Back to app leads to the root (sign-in when signed out)', async () => {
    await mount('/join/ABC123');
    const { screen, fireEvent } = lib;
    await fireEvent.press(await screen.findByLabelText('Back to app'));
    expect(await screen.findByLabelText('Continue (placeholder)')).toBeTruthy();
    expect(screen.queryByTestId('join-screen')).toBeNull();
  });

  it('is pushed on top of the app when signed in, and back returns to it', async () => {
    await mount('/');
    await signIn();
    const { screen, fireEvent, waitFor } = lib;
    await lib.act(async () => router.push('/invite/tok-1'));
    expect(await screen.findByTestId('invite-screen')).toBeTruthy();
    expect(router.canGoBack()).toBe(true);
    await fireEvent.press(screen.getByLabelText('Back to app'));
    await waitFor(() => expect(screen.queryByTestId('invite-screen')).toBeNull());
    expect(screen.getByLabelText('Play tab')).toBeTruthy();
  });
});

describe('accessibility', () => {
  it('gives buttons a label, a button role and a 44 pt minimum touch target', async () => {
    await mount('/');
    const { screen } = lib;
    const button = await screen.findByLabelText('Continue (placeholder)');
    expect(button.props.accessibilityRole).toBe('button');
    const style = StyleSheet.flatten(button.props.style);
    expect(style.minHeight).toBeGreaterThanOrEqual(44);
    expect(style.minWidth).toBeGreaterThanOrEqual(44);
  });

  it('gives every tab an accessibility label and a 44 pt minimum touch target', async () => {
    await mount('/');
    await signIn();
    const { screen } = lib;
    for (const name of ['Play', 'Decks', 'Stats', 'More']) {
      expect(screen.getByLabelText(`${name} tab`)).toBeTruthy();
      // The item style is applied to the wrapper around the tab button.
      let node = screen.getByTestId(`tab-${name.toLowerCase()}`).parent;
      let minHeight: number | undefined;
      let minWidth: number | undefined;
      while (node && minHeight === undefined) {
        const style = StyleSheet.flatten(node.props.style);
        minHeight = style?.minHeight;
        minWidth = style?.minWidth;
        node = node.parent;
      }
      expect(minHeight).toBeGreaterThanOrEqual(44);
      expect(minWidth).toBeGreaterThanOrEqual(44);
    }
  });
});
