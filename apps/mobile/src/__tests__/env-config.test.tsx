import { render, screen } from '@testing-library/react-native';
import { ConfigErrorScreen } from '../components/ConfigErrorScreen';
import { API_BASE_URL_VAR, parseApiBaseUrl } from '../env';

describe('parseApiBaseUrl (S-1.7.4 AC2)', () => {
  it.each([undefined, '', '   '])('reports %j as missing', (raw) => {
    expect(parseApiBaseUrl(raw)).toEqual({
      ok: false,
      variable: 'EXPO_PUBLIC_API_BASE_URL',
      problem: 'missing',
    });
  });

  it.each(['192.168.1.20:8000', 'ftp://host/api/v1', 'not a url', '/api/v1'])(
    'reports %j as malformed',
    (raw) => {
      expect(parseApiBaseUrl(raw)).toMatchObject({ ok: false, problem: 'malformed' });
    },
  );

  it.each(['http://192.168.1.20:8000/api/v1', 'https://api.example.com/api/v1'])(
    'accepts %j and trims whitespace',
    (raw) => {
      expect(parseApiBaseUrl(`  ${raw} `)).toEqual({ ok: true, apiBaseUrl: raw });
    },
  );

  it('is set to a valid value by the jest setup, so the app renders in tests', () => {
    expect(API_BASE_URL_VAR).toBe('EXPO_PUBLIC_API_BASE_URL');
    expect(process.env.EXPO_PUBLIC_API_BASE_URL).toBeTruthy();
  });
});

describe('ConfigErrorScreen', () => {
  it('has a heading, names the variable and gives a fix hint when the value is missing', async () => {
    await render(<ConfigErrorScreen problem="missing" />);
    expect(screen.getByRole('header', { name: 'Configuration error' })).toBeTruthy();
    expect(screen.getByTestId('config-error-variable')).toHaveTextContent(
      'EXPO_PUBLIC_API_BASE_URL',
    );
    expect(screen.getByText(/not set/)).toBeTruthy();
    expect(screen.getByText(/Example: EXPO_PUBLIC_API_BASE_URL=http:\/\//)).toBeTruthy();
  });

  it('says the value is invalid when it is malformed', async () => {
    await render(<ConfigErrorScreen problem="malformed" />);
    expect(screen.getByText(/not a valid http\(s\) URL/)).toBeTruthy();
  });
});
