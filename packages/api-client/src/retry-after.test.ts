// S-2.1.6 decision 5: ApiError.retryAfter (seconds) is parsed from the Retry-After header as
// delta-seconds or an HTTP date, measured from now and clamped at 0; anything else is null.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, createApiClient } from './index';
import { apiErrorFromResponse } from './errors';

function envelopeBody(code: string) {
  return { error: { code, message: `${code} message`, details: null, request_id: 'req-1' } };
}

function response(status: number, headers: Record<string, string>): Response {
  return new Response(JSON.stringify(envelopeBody('rate_limited')), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  });
}

function parse(headers: Record<string, string>, status = 429): ApiError {
  return apiErrorFromResponse(response(status, headers), envelopeBody('rate_limited'));
}

afterEach(() => {
  vi.useRealTimers();
});

describe('ApiError.retryAfter: delta-seconds', () => {
  it.each([
    ['120', 120],
    ['30', 30],
    ['1', 1],
    ['0', 0],
    ['3600', 3600],
  ])('Retry-After: %s gives %d seconds', (header, expected) => {
    expect(parse({ 'Retry-After': header }).retryAfter).toBe(expected);
  });

  it('is exposed on a 429 envelope error alongside code, status and message', () => {
    const error = parse({ 'Retry-After': '120' });
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(429);
    expect(error.code).toBe('rate_limited');
    expect(error.message).toBe('rate_limited message');
    expect(error.retryAfter).toBe(120);
  });

  it('is parsed on other statuses too (a 503 may carry Retry-After)', () => {
    expect(parse({ 'Retry-After': '7' }, 503).retryAfter).toBe(7);
  });

  it('is parsed when the body is not an error envelope (unexpected_response)', () => {
    const res = new Response('Too Many Requests', {
      status: 429,
      headers: { 'Retry-After': '45' },
    });
    const error = apiErrorFromResponse(res, 'Too Many Requests');
    expect(error.code).toBe('unexpected_response');
    expect(error.retryAfter).toBe(45);
  });
});

describe('ApiError.retryAfter: HTTP date', () => {
  it('measures a future date from now, in seconds', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-11-16T12:00:00.000Z'));
    expect(parse({ 'Retry-After': 'Mon, 16 Nov 2026 12:02:00 GMT' }).retryAfter).toBe(120);
  });

  it('clamps a date in the past at 0', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-11-16T12:00:00.000Z'));
    expect(parse({ 'Retry-After': 'Mon, 16 Nov 2026 11:00:00 GMT' }).retryAfter).toBe(0);
  });
});

describe('ApiError.retryAfter: null', () => {
  it('is null when the header is missing', () => {
    expect(parse({}).retryAfter).toBeNull();
  });

  it.each(['', 'soon', '-5', '1.5', '1e3', '12 seconds', 'NaN', 'Infinity'])(
    'is null for the unparseable value %j',
    (header) => {
      expect(parse({ 'Retry-After': header }).retryAfter).toBeNull();
    },
  );

  it('is null on an ApiError built without it', () => {
    expect(new ApiError({ status: 500, code: 'x', message: 'm' }).retryAfter).toBeNull();
  });

  it('is null on a network ApiError', () => {
    const error = new ApiError({ status: 0, code: 'network_error', message: 'offline' });
    expect(error.retryAfter).toBeNull();
  });
});

describe('ApiError.retryAfter through the client', () => {
  it('a 429 from a call throws an ApiError with retryAfter 120', async () => {
    const fetchImpl = (async () =>
      response(429, { 'Retry-After': '120' })) as unknown as typeof fetch;
    const api = createApiClient({ baseUrl: 'https://api.test', fetch: fetchImpl });
    let caught: unknown;
    try {
      await api.GET('/api/v1/health/live');
    } catch (error) {
      caught = error;
    }
    expect(caught).toBeInstanceOf(ApiError);
    expect((caught as ApiError).status).toBe(429);
    expect((caught as ApiError).retryAfter).toBe(120);
  });
});
