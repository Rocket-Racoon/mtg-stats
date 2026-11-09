import { render, screen } from '@testing-library/react-native';
import Index from '../app/index';

describe('Index screen (AC4 render smoke)', () => {
  it('renders the MTG Stats heading', async () => {
    await render(<Index />);
    expect(screen.getByText('MTG Stats')).toBeTruthy();
  });

  it('renders the three workspace package placeholders', async () => {
    await render(<Index />);
    const text = screen.getByTestId('linked-packages');
    expect(text).toHaveTextContent(/api-client/);
    expect(text).toHaveTextContent(/card-utils/);
    expect(text).toHaveTextContent(/game-core/);
  });

  it('renders the API base URL', async () => {
    await render(<Index />);
    expect(screen.getByTestId('api-base-url')).toBeTruthy();
  });

  it('shows the gesture test link in dev', async () => {
    expect(__DEV__).toBe(true);
    await render(<Index />);
    expect(screen.getByText('Gesture test')).toBeTruthy();
  });

  it('hides the gesture test link when __DEV__ is false', async () => {
    const g = globalThis as unknown as { __DEV__: boolean };
    const original = g.__DEV__;
    g.__DEV__ = false;
    try {
      await render(<Index />);
      expect(screen.queryByText('Gesture test')).toBeNull();
    } finally {
      g.__DEV__ = original;
    }
  });
});
