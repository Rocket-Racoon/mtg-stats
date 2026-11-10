// @vitest-environment jsdom
import { QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { createElement, type ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';
import {
  ApiClientProvider,
  createApiClient,
  createQueryClient,
  useApiClient,
  useHealthLive,
  useHealthReady,
} from '../index';

function setup(fetchImpl: typeof fetch) {
  const client = createApiClient({ baseUrl: 'http://api.test', fetch: fetchImpl });
  const qc = createQueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(
      QueryClientProvider,
      { client: qc },
      createElement(ApiClientProvider, { client }, children),
    );
  return { wrapper, qc };
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });

describe('system hooks', () => {
  it('useHealthLive returns the liveness body under the system key', async () => {
    const fetchImpl = vi.fn(() => Promise.resolve(json({ status: 'ok' })));
    const { wrapper, qc } = setup(fetchImpl as unknown as typeof fetch);
    const { result } = renderHook(() => useHealthLive(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual({ status: 'ok' });
    expect(qc.getQueryCache().getAll()[0]?.queryKey).toEqual(['system', 'healthLive', {}]);
  });

  it('useHealthReady surfaces a 503 ready body as an ApiError without retrying', async () => {
    const fetchImpl = vi.fn(() =>
      Promise.resolve(
        json({ status: 'error', version: 'dev', checks: { database: 'ok', redis: 'error' } }, 503),
      ),
    );
    const { wrapper } = setup(fetchImpl as unknown as typeof fetch);
    const { result } = renderHook(() => useHealthReady(), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toMatchObject({
      status: 503,
      code: 'unexpected_response',
      details: { checks: { database: 'ok', redis: 'error' } },
    });
  });

  it('useApiClient throws without a provider', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    expect(() => renderHook(() => useApiClient())).toThrow(/ApiClientProvider/);
    spy.mockRestore();
  });
});
