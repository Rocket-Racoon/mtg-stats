// @vitest-environment jsdom
// S-2.1.6 decision 9: useSignIn() and useRegister() are mutations, useInvitePreview(token) is a
// query that does not retry a 410. Inline-error mutations opt out of the error toast with
// meta.errorToast = false.
import { QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import { createElement, type ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';
import {
  ApiClientProvider,
  ApiError,
  createApiClient,
  createQueryClient,
  useInvitePreview,
  useRegister,
  useSignIn,
} from '../index';

interface Call {
  path: string;
  method: string;
  credentials: RequestCredentials;
  body: unknown;
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
const failure = (status: number, code: string, details: unknown = null) =>
  json({ error: { code, message: `${code} message`, details, request_id: 'r' } }, status);
const tokenBody = {
  access_token: 'tok-1', // secrets-check: allow
  token_type: 'bearer',
  expires_in: 900,
};

function setup(handler: (call: Call) => Response) {
  const calls: Call[] = [];
  const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input, init);
    const text = await request.text();
    const call: Call = {
      path: new URL(request.url).pathname,
      method: request.method,
      credentials: request.credentials,
      body: text === '' ? null : JSON.parse(text),
    };
    calls.push(call);
    return handler(call);
  });
  const client = createApiClient({
    baseUrl: 'http://api.test',
    fetch: fetchImpl as unknown as typeof fetch,
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

describe('useSignIn', () => {
  it('POSTs username and password to /api/v1/auth/login and resolves the token response', async () => {
    const { wrapper, calls } = setup(() => json(tokenBody));
    const { result } = renderHook(() => useSignIn(), { wrapper });
    let data: unknown;
    await act(async () => {
      data = await result.current.mutateAsync({ username: 'ada', password: 'correct horse' });
    });
    expect(data).toEqual(tokenBody);
    expect(calls).toHaveLength(1);
    expect(calls[0]!.path).toBe('/api/v1/auth/login');
    expect(calls[0]!.method).toBe('POST');
    expect(calls[0]!.credentials).toBe('include');
    expect(calls[0]!.body).toEqual({ username: 'ada', password: 'correct horse' });
  });

  it('rejects with the ApiError of a 401 and does not retry', async () => {
    const { wrapper, calls } = setup(() => failure(401, 'invalid_credentials'));
    const { result } = renderHook(() => useSignIn(), { wrapper });
    await act(async () => {
      result.current.mutate({ username: 'ada', password: 'nope' });
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(ApiError);
    expect(result.current.error).toMatchObject({ status: 401, code: 'invalid_credentials' });
    expect(calls).toHaveLength(1);
  });

  it('opts out of the error toast with meta.errorToast = false', async () => {
    const { wrapper, qc } = setup(() => json(tokenBody));
    const { result } = renderHook(() => useSignIn(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({ username: 'ada', password: 'correct horse' });
    });
    expect(qc.getMutationCache().getAll()[0]!.meta).toMatchObject({ errorToast: false });
  });
});

describe('useRegister', () => {
  const body = {
    invite_token: 'inv-1', // secrets-check: allow
    username: 'ada',
    password: 'correct horse battery',
    display_name: 'Ada',
    email: 'ada@example.com',
  };

  it('POSTs the registration body to /api/v1/auth/register and resolves the token response', async () => {
    const { wrapper, calls } = setup(() => json(tokenBody, 201));
    const { result } = renderHook(() => useRegister(), { wrapper });
    let data: unknown;
    await act(async () => {
      data = await result.current.mutateAsync(body);
    });
    expect(data).toEqual(tokenBody);
    expect(calls[0]!.path).toBe('/api/v1/auth/register');
    expect(calls[0]!.method).toBe('POST');
    expect(calls[0]!.credentials).toBe('include');
    expect(calls[0]!.body).toEqual(body);
  });

  it.each([
    [410, 'invite_no_longer_valid'],
    [409, 'username_taken'],
  ])('rejects with the ApiError for a %d %s', async (status, code) => {
    const { wrapper } = setup(() => failure(status, code));
    const { result } = renderHook(() => useRegister(), { wrapper });
    await act(async () => {
      result.current.mutate(body);
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toMatchObject({ status, code });
  });

  it('opts out of the error toast with meta.errorToast = false', async () => {
    const { wrapper, qc } = setup(() => json(tokenBody, 201));
    const { result } = renderHook(() => useRegister(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync(body);
    });
    expect(qc.getMutationCache().getAll()[0]!.meta).toMatchObject({ errorToast: false });
  });
});

describe('useInvitePreview', () => {
  const preview = { inviter_display_name: 'Ada Lovelace', expires_at: '2026-12-01T00:00:00Z' };

  it('POSTs the token in the body to /api/v1/invites/preview and returns the preview', async () => {
    const { wrapper, calls } = setup(() => json(preview));
    const { result } = renderHook(() => useInvitePreview('inv-123'), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(preview);
    expect(calls).toHaveLength(1);
    expect(calls[0]!.path).toBe('/api/v1/invites/preview');
    expect(calls[0]!.method).toBe('POST');
    expect(calls[0]!.body).toEqual({ token: 'inv-123' });
  });

  it('does not retry a 410: one request, then the ApiError', async () => {
    const { wrapper, calls } = setup(() => failure(410, 'invite_no_longer_valid'));
    const { result } = renderHook(() => useInvitePreview('inv-gone'), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toMatchObject({ status: 410, code: 'invite_no_longer_valid' });
    expect(calls).toHaveLength(1);
  });

  it('uses a different cache entry per token', async () => {
    const { wrapper, qc } = setup(() => json(preview));
    const first = renderHook(() => useInvitePreview('aaa'), { wrapper });
    const second = renderHook(() => useInvitePreview('bbb'), { wrapper });
    await waitFor(() => {
      expect(first.result.current.isSuccess).toBe(true);
      expect(second.result.current.isSuccess).toBe(true);
    });
    expect(qc.getQueryCache().getAll()).toHaveLength(2);
  });
});
