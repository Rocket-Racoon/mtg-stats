import { render, screen } from '@testing-library/react-native';
import { Text } from 'react-native';
import { AppProviders } from '../components/AppProviders';

jest.mock('../env', () => ({
  API_BASE_URL_VAR: 'EXPO_PUBLIC_API_BASE_URL',
  env: { ok: false, variable: 'EXPO_PUBLIC_API_BASE_URL', problem: 'missing' },
}));
jest.mock('../api', () => ({ api: null }));

describe('AppProviders without EXPO_PUBLIC_API_BASE_URL (S-1.7.4 AC2)', () => {
  it('shows the config error screen naming the variable instead of the app', async () => {
    await render(
      <AppProviders>
        <Text testID="child">app</Text>
      </AppProviders>,
    );
    expect(screen.queryByTestId('child')).toBeNull();
    expect(screen.getByRole('header', { name: 'Configuration error' })).toBeTruthy();
    expect(screen.getByTestId('config-error-variable')).toHaveTextContent(
      'EXPO_PUBLIC_API_BASE_URL',
    );
  });
});
