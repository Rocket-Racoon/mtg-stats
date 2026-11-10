import { renderRouter } from 'expo-router/testing-library';
import { Text } from 'react-native';
import Layout from '../app/_layout';
import GestureTestRoute from '../app/dev/gesture-test';

const g = globalThis as unknown as { __DEV__: boolean };
const original = g.__DEV__;

async function mount() {
  return await renderRouter(
    {
      _layout: Layout,
      index: () => <Text>home</Text>,
      'dev/gesture-test': GestureTestRoute,
    },
    { initialUrl: '/dev/gesture-test' },
  );
}

describe('navigating to /dev/gesture-test through the real layout', () => {
  afterEach(() => {
    g.__DEV__ = original;
  });

  it('renders the gesture screen in dev (control)', async () => {
    g.__DEV__ = true;
    const view = await mount();
    expect(await view.findByTestId('gesture-box')).toBeTruthy();
  });

  it('does not render the gesture screen when __DEV__ is false', async () => {
    g.__DEV__ = false;
    const view = await mount();
    expect(await view.findByText('home')).toBeTruthy();
    expect(view.queryByTestId('gesture-box')).toBeNull();
  });
});
