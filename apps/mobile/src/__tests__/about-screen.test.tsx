import {
  FAN_CONTENT_DISCLAIMER,
  FAN_CONTENT_DISCLAIMER_TEXT,
  FAN_CONTENT_POLICY_URL,
  SCRYFALL_CREDIT_TEXT,
  SCRYFALL_IMAGES_NOTE,
  SCRYFALL_URL,
} from '@mtg-stats/card-utils';
import { router } from 'expo-router';
import * as lib from 'expo-router/testing-library';
import { Linking } from 'react-native';
import { act } from '@testing-library/react-native';

jest.setTimeout(60000);

// Jest does not load app.json, so the Expo config is stubbed with a known version.
jest.mock('expo-constants', () => ({
  __esModule: true,
  default: { expoConfig: { version: '1.2.3' } },
}));

const APP_DIR = './src/app';

async function expectAboutContent() {
  const { screen } = lib;
  expect(await screen.findByRole('header', { name: 'About' })).toBeTruthy();
  // T-1.7.5.2: rendered notice text equals the shared module's exports.
  expect(screen.getByTestId('fan-content-notice')).toHaveTextContent(FAN_CONTENT_DISCLAIMER_TEXT, {
    exact: true,
  });
  expect(screen.getByTestId('scryfall-credit')).toHaveTextContent(SCRYFALL_CREDIT_TEXT, {
    exact: true,
  });
  expect(screen.getByText(SCRYFALL_IMAGES_NOTE)).toBeTruthy();
  expect(screen.getByTestId('app-version')).toHaveTextContent('1.2.3');
}

describe('About from the More tab', () => {
  it('opens from More, shows the shared notices and returns to More on back', async () => {
    await lib.renderRouter(APP_DIR, { initialUrl: '/' });
    const { screen, fireEvent } = lib;
    await fireEvent.press(await screen.findByLabelText('Continue (placeholder)'));
    await fireEvent.press(await screen.findByLabelText('More'));
    await fireEvent.press(await screen.findByLabelText('About'));
    await expectAboutContent();
    expect(screen).toHavePathname('/more/about');
    await act(async () => router.back());
    expect(await screen.findByLabelText('Sign out')).toBeTruthy();
    expect(screen).toHavePathname('/more');
  });
});

describe('About from the sign-in stack', () => {
  it('opens while signed out and returns to sign-in on back', async () => {
    await lib.renderRouter(APP_DIR, { initialUrl: '/' });
    const { screen, fireEvent } = lib;
    await fireEvent.press(await screen.findByLabelText('About'));
    await expectAboutContent();
    expect(screen).toHavePathname('/about');
    await act(async () => router.back());
    expect(await screen.findByLabelText('Continue (placeholder)')).toBeTruthy();
  });
});

describe('About links', () => {
  it('opens the policy and Scryfall links in the system browser', async () => {
    const open = jest.spyOn(Linking, 'openURL').mockResolvedValue();
    await lib.renderRouter(APP_DIR, { initialUrl: '/about' });
    const { screen, fireEvent } = lib;
    const policy = await screen.findByRole('link', { name: 'Fan Content Policy' });
    await fireEvent.press(policy);
    expect(open).toHaveBeenLastCalledWith(FAN_CONTENT_POLICY_URL);
    await fireEvent.press(screen.getByRole('link', { name: 'Scryfall' }));
    expect(open).toHaveBeenLastCalledWith(SCRYFALL_URL);
    expect(FAN_CONTENT_DISCLAIMER.some((s) => s.kind === 'link')).toBe(true);
    open.mockRestore();
  });
});
