import { render, screen } from '@testing-library/react-native';
import GestureTestRoute from '../app/dev/gesture-test';

// Redirect is mocked so the test can observe the redirect target without a navigator.
const mockRedirect = jest.fn();
jest.mock('expo-router', () => ({
  ...jest.requireActual<object>('expo-router'),
  Redirect: (props: { href: string }) => {
    mockRedirect(props.href);
    return null;
  },
}));

describe('dev gesture route in a production build', () => {
  const g = globalThis as unknown as { __DEV__: boolean };
  const original = g.__DEV__;
  afterEach(() => {
    g.__DEV__ = original;
    mockRedirect.mockClear();
  });

  it('redirects to / and does not render the gesture screen when __DEV__ is false', async () => {
    g.__DEV__ = false;
    await render(<GestureTestRoute />);
    expect(mockRedirect).toHaveBeenCalledWith('/');
    expect(screen.queryByTestId('gesture-box')).toBeNull();
    expect(screen.queryByTestId('move-button')).toBeNull();
  });
});
