import { useHealthReady } from '@mtg-stats/api-client';
import { render, screen, waitFor } from '@testing-library/react-native';
import { Text } from 'react-native';
import { AppProviders } from '../components/AppProviders';

const READY = { status: 'ok', version: 'dev', checks: { database: 'ok', redis: 'ok' } };

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

function Probe() {
  const ready = useHealthReady();
  if (ready.isPending) return <Text testID="probe">pending</Text>;
  if (ready.isError) return <Text testID="probe">error</Text>;
  return <Text testID="probe">{`status ${ready.data.status}`}</Text>;
}

describe('AppProviders (S-1.7.4 provider wiring)', () => {
  afterEach(() => jest.restoreAllMocks());

  it('renders a hook from the api-client package: one react, one TanStack copy', async () => {
    const fetchMock = jest.spyOn(globalThis, 'fetch').mockResolvedValue(json(READY));
    await render(
      <AppProviders>
        <Probe />
      </AppProviders>,
    );
    await waitFor(() => expect(screen.getByTestId('probe')).toHaveTextContent('status ok'));
    const request = fetchMock.mock.calls[0]?.[0] as Request;
    expect(request.url).toBe('http://localhost:8000/api/v1/health/ready');
  });
});
