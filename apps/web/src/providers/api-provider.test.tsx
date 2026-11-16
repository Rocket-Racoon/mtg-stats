import { render, screen } from '@testing-library/react';
import { createApiClient, useApiClient } from '@mtg-stats/api-client';
import { describe, expect, it } from 'vitest';
import { api } from '../api';
import { AppProviders } from './AppProviders';

function Probe({ onClient }: { onClient: (client: unknown) => void }) {
  onClient(useApiClient());
  return <p>probe rendered</p>;
}

describe('AppProviders mounts ApiClientProvider (S-1.5.3)', () => {
  it('lets a component call useApiClient() without its own provider', () => {
    render(
      <AppProviders>
        <Probe onClient={() => undefined} />
      </AppProviders>,
    );
    expect(screen.getByText('probe rendered')).toBeInTheDocument();
  });

  it("uses the app's api client by default", () => {
    let seen: unknown;
    render(
      <AppProviders>
        <Probe
          onClient={(client) => {
            seen = client;
          }}
        />
      </AppProviders>,
    );
    expect(seen).toBe(api);
  });

  it('returns an injected apiClient prop instead', () => {
    const injected = createApiClient({ baseUrl: 'https://injected.test' });
    let seen: unknown;
    render(
      <AppProviders apiClient={injected}>
        <Probe
          onClient={(client) => {
            seen = client;
          }}
        />
      </AppProviders>,
    );
    expect(seen).toBe(injected);
    expect(seen).not.toBe(api);
  });
});
