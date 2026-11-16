// @vitest-environment jsdom
// S-2.1.8 decision 5: useChangePassword() is a mutation that POSTs {current_password, new_password}
// to /api/v1/auth/change-password with the Bearer token and resolves to the token response. The
// page shows its errors inline, so meta.errorToast = false.
import { QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import { createElement, type ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';
import {
  ApiClientProvider,
  ApiError,
  createApiClient,
  createQueryClient,
  useChangePassword,
} from '../index';

interface Call {
  path: string;
  method: string;
  authorization: string | null;
  body: unknown;
}

const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  });
const failure = (
  status: number,
  code: string,
  details: unknown = null,
  headers: Record<string, string> = {},
) =>
  json({ error: { code, message: `${code} message`, details, request_id: 'r' } }, status, headers);
const tokenBody = {
  access_token: 'new-access', // secrets-check: allow
  token_type: 'bearer',
  expires_in: 900,
};
const input = { current_password: 'old password 123', new_password: 'brand new password 456' }; // secrets-check: allow

function setup(handler: (call: Call) => Response) {
  const calls: Call[] = [];
  const fetchImpl = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
    const req = new Request(request, init);
    const text = await req.text();
    const call: Call = {
      path: new URL(req.url).pathname,
      method: req.method,
      authorization: req.headers.get('Authorization'),
      body: text === '' ? null : JSON.parse(text),
    };
    calls.push(call);
    return handler(call);
  });
  const client = createApiClient({
    baseUrl: 'http://api.test',
    fetch: fetchImpl as unknown as typeof fetch,
    tokenStore: {
      getAccessToken: () => 'current-access', // secrets-check: allow
      setTokens: () => undefined,
      clear: () => undefined,
    },
  });
  const qc = createQueryClient();
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(
      QueryClientProvider,
      { client: qc },
      createElement(ApiClientProvider, { client }, children),
    );
  return { wrapper, qc, calls };
}

describe('useChangePassword', () => {
  it('POSTs both passwords to /api/v1/auth/change-password with the Bearer token and resolves the token response', async () => {
    const { wrapper, calls } = setup(() => json(tokenBody));
    const { result } = renderHook(() => useChangePassword(), { wrapper });
    let data: unknown;
    await act(async () => {
      data = await result.current.mutateAsync(input);
    });
    expect(data).toEqual(tokenBody);
    expect(calls).toHaveLength(1);
    expect(calls[0]!.path).toBe('/api/v1/auth/change-password');
    expect(calls[0]!.method).toBe('POST');
    expect(calls[0]!.authorization).toBe('Bearer current-access');
    expect(calls[0]!.body).toEqual(input);
  });

  it.each([
    [
      400,
      'invalid_current_password',
      {
        fields: [
          {
            loc: ['body', 'current_password'],
            path: 'body.current_password',
            message: 'Current password is incorrect.',
          },
        ],
      },
    ],
    [
      422,
      'validation_error',
      {
        fields: [
          { loc: ['body', 'new_password'], path: 'body.new_password', message: 'Too short.' },
        ],
      },
    ],
    [429, 'rate_limited', null],
  ])('rejects with the ApiError of a %d %s and does not retry', async (status, code, details) => {
    const { wrapper, calls } = setup(() =>
      failure(status, code, details, status === 429 ? { 'Retry-After': '120' } : {}),
    );
    const { result } = renderHook(() => useChangePassword(), { wrapper });
    await act(async () => {
      result.current.mutate(input);
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(ApiError);
    expect(result.current.error).toMatchObject({ status, code });
    expect(calls).toHaveLength(1);
  });

  it('opts out of the error toast with meta.errorToast = false', async () => {
    const { wrapper, qc } = setup(() => json(tokenBody));
    const { result } = renderHook(() => useChangePassword(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync(input);
    });
    expect(qc.getMutationCache().getAll()[0]!.meta).toMatchObject({ errorToast: false });
  });
});
