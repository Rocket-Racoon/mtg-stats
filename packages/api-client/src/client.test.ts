import { describe, expect, expectTypeOf, it, vi } from 'vitest';
import { ApiError, createApiClient } from './index';
import type { components } from './index';

const BASE = 'https://api.test';

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init.headers },
  });
}

function clientWith(fetchImpl: typeof fetch) {
  return createApiClient({ baseUrl: BASE, fetch: fetchImpl });
}

async function caught(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  throw new Error('expected the call to throw');
}

describe('createApiClient success', () => {
  it('resolves to the typed response data and calls the right URL', async () => {
    const fetchMock = vi.fn<(req: Request) => Promise<Response>>(async () =>
      jsonResponse({ status: 'ok' }),
    );
    const api = clientWith(fetchMock as unknown as typeof fetch);

    const data = await api.GET('/api/v1/health/live');

    expect(data).toEqual({ status: 'ok' });
    expectTypeOf(data).toEqualTypeOf<components['schemas']['LiveResponse']>();
    const request = fetchMock.mock.calls[0]![0];
    expect(request.method).toBe('GET');
    expect(request.url).toBe(`${BASE}/api/v1/health/live`);
  });

  it('sends the configured headers', async () => {
    const fetchMock = vi.fn<(req: Request) => Promise<Response>>(async () =>
      jsonResponse({ status: 'ok' }),
    );
    const api = createApiClient({
      baseUrl: BASE,
      fetch: fetchMock as unknown as typeof fetch,
      headers: { 'X-Client': 'test' },
    });

    await api.GET('/api/v1/health/live');

    expect(fetchMock.mock.calls[0]![0].headers.get('X-Client')).toBe('test');
  });

  it('uses the platform fetch when none is given', async () => {
    const fetchMock = vi.fn<(req: Request) => Promise<Response>>(async () =>
      jsonResponse({ status: 'ok' }),
    );
    vi.stubGlobal('fetch', fetchMock);
    try {
      await createApiClient({ baseUrl: BASE }).GET('/api/v1/health/live');
      expect(fetchMock).toHaveBeenCalledTimes(1);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('runs registered middleware', async () => {
    const fetchMock = vi.fn<(req: Request) => Promise<Response>>(async () =>
      jsonResponse({ status: 'ok' }),
    );
    const api = clientWith(fetchMock as unknown as typeof fetch);
    api.use({
      onRequest({ request }) {
        request.headers.set('Authorization', 'Bearer t');
        return request;
      },
    });

    await api.GET('/api/v1/health/live');

    expect(fetchMock.mock.calls[0]![0].headers.get('Authorization')).toBe('Bearer t');
  });
});

describe('createApiClient errors', () => {
  it('throws an ApiError built from the error envelope', async () => {
    const api = clientWith((async () =>
      jsonResponse(
        {
          error: {
            code: 'validation_error',
            message: 'Invalid request.',
            details: { fields: [{ path: 'body.x' }] },
            request_id: 'req-1',
          },
        },
        { status: 422, headers: { 'X-Request-ID': 'req-header' } },
      )) as typeof fetch);

    const error = await caught(api.GET('/api/v1/health/live'));

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toBeInstanceOf(Error);
    expect(error).toMatchObject({
      name: 'ApiError',
      status: 422,
      code: 'validation_error',
      message: 'Invalid request.',
      details: { fields: [{ path: 'body.x' }] },
      requestId: 'req-1',
    });
  });

  it('maps a null details and missing request_id to null / the header', async () => {
    const api = clientWith((async () =>
      jsonResponse(
        { error: { code: 'not_found', message: 'Nope.', details: null } },
        { status: 404, headers: { 'X-Request-ID': 'req-header' } },
      )) as typeof fetch);

    const error = (await caught(api.GET('/api/v1/health/live'))) as ApiError;

    expect(error.details).toBeNull();
    expect(error.requestId).toBe('req-header');
  });

  it('treats a non-envelope HTML body as unexpected_response', async () => {
    const api = clientWith(
      (async () =>
        new Response('<html>Bad gateway</html>', {
          status: 502,
          statusText: 'Bad Gateway',
          headers: { 'X-Request-ID': 'req-9' },
        })) as typeof fetch,
    );

    const error = (await caught(api.GET('/api/v1/health/live'))) as ApiError;

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 502,
      code: 'unexpected_response',
      message: 'Bad Gateway',
      details: null,
      requestId: 'req-9',
    });
  });

  it('treats an empty error body as unexpected_response with a generic message', async () => {
    const api = clientWith((async () => new Response(null, { status: 500 })) as typeof fetch);

    const error = (await caught(api.GET('/api/v1/health/live'))) as ApiError;

    expect(error).toMatchObject({
      status: 500,
      code: 'unexpected_response',
      details: null,
      requestId: null,
    });
    expect(error.message).toContain('500');
  });

  it('treats a 503 health body (no envelope) as unexpected_response and keeps the body', async () => {
    const body = { status: 'unavailable', checks: { database: 'down' } };
    const api = clientWith((async () => jsonResponse(body, { status: 503 })) as typeof fetch);

    const error = (await caught(api.GET('/api/v1/health/ready'))) as ApiError;

    expect(error).toMatchObject({ status: 503, code: 'unexpected_response', details: body });
  });

  it('lets middleware see the non-2xx response before the error is thrown', async () => {
    const seen: number[] = [];
    const api = clientWith((async () =>
      jsonResponse(
        { error: { code: 'unauthorized', message: 'x', details: null } },
        { status: 401 },
      )) as typeof fetch);
    api.use({
      onResponse({ response }) {
        seen.push(response.status);
        return undefined;
      },
    });

    await caught(api.GET('/api/v1/health/live'));

    expect(seen).toEqual([401]);
  });

  it('wraps a fetch rejection as a network_error ApiError keeping the cause', async () => {
    const original = new TypeError('Failed to fetch');
    const api = clientWith((async () => {
      throw original;
    }) as typeof fetch);

    const error = (await caught(api.GET('/api/v1/health/live'))) as ApiError;

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 0,
      code: 'network_error',
      message: 'Failed to fetch',
      details: null,
      requestId: null,
    });
    expect(error.cause).toBe(original);
  });

  it('propagates an AbortError unchanged', async () => {
    const abort = new DOMException('The operation was aborted.', 'AbortError');
    const api = clientWith((async () => {
      throw abort;
    }) as typeof fetch);

    const error = await caught(api.GET('/api/v1/health/live'));

    expect(error).toBe(abort);
    expect(error).not.toBeInstanceOf(ApiError);
  });

  it('falls back to the X-Request-ID header when request_id is an empty string', async () => {
    const api = clientWith((async () =>
      jsonResponse(
        { error: { code: 'internal_error', message: 'Boom.', details: null, request_id: '' } },
        { status: 500, headers: { 'X-Request-ID': 'req-header' } },
      )) as typeof fetch);

    const error = (await caught(api.GET('/api/v1/health/live'))) as ApiError;

    expect(error.requestId).toBe('req-header');
  });

  it('uses a null requestId when request_id is empty and there is no header', async () => {
    const api = clientWith((async () =>
      jsonResponse(
        { error: { code: 'internal_error', message: 'Boom.', details: null, request_id: '' } },
        { status: 500 },
      )) as typeof fetch);

    const error = (await caught(api.GET('/api/v1/health/live'))) as ApiError;

    expect(error.requestId).toBeNull();
  });

  it('throws unexpected_response with the real status for a 200 with an HTML body', async () => {
    const api = clientWith(
      (async () =>
        new Response('<!doctype html><html></html>', {
          status: 200,
          headers: { 'Content-Type': 'text/html', 'X-Request-ID': 'req-7' },
        })) as typeof fetch,
    );

    const error = (await caught(api.GET('/api/v1/health/live'))) as ApiError;

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 200, code: 'unexpected_response', requestId: 'req-7' });
    expect(error.cause).toBeInstanceOf(SyntaxError);
  });

  it('resolves to undefined for a 204 with no body', async () => {
    const api = clientWith((async () => new Response(null, { status: 204 })) as typeof fetch);

    await expect(api.GET('/api/v1/health/live')).resolves.toBeUndefined();
  });
});
