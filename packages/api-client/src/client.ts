import createClient, {
  type Client,
  type ClientOptions,
  type ClientPathsWithMethod,
  type FetchResponse,
  type MaybeOptionalInit,
  type Middleware,
} from 'openapi-fetch';
import {
  ApiError,
  NETWORK_ERROR,
  UNEXPECTED_RESPONSE,
  apiErrorFromResponse,
  isAbortError,
} from './errors';
import { createAuthMiddleware, type TokenStore } from './auth';
import type { paths } from './generated/schema';

type HttpMethod = 'get' | 'put' | 'post' | 'delete' | 'options' | 'head' | 'patch' | 'trace';

type RequiredKeysOf<T> = {
  [K in keyof T]-?: object extends Pick<T, K> ? never : K;
}[keyof T];

type InitParam<Init> =
  RequiredKeysOf<Init> extends never
    ? [(Init & { [key: string]: unknown })?]
    : [Init & { [key: string]: unknown }];

type Operation<P, Path, M extends HttpMethod> = Path extends keyof P
  ? M extends keyof P[Path]
    ? P[Path][M]
    : never
  : never;

type SuccessData<R> = R extends { error?: never; data: infer D } ? D : never;

/** One typed operation method. Resolves to the response data and throws ApiError on failure. */
export type ApiMethod<P extends object, M extends HttpMethod> = <
  Path extends ClientPathsWithMethod<Client<P>, M>,
  Init extends MaybeOptionalInit<P[Path & keyof P], M & keyof P[Path & keyof P]>,
>(
  url: Path,
  ...init: InitParam<Init>
) => Promise<
  SuccessData<
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    FetchResponse<Operation<P, Path, M> & Record<string | number, any>, Init, `${string}/${string}`>
  >
>;

export interface ApiClient<P extends object = paths> {
  GET: ApiMethod<P, 'get'>;
  PUT: ApiMethod<P, 'put'>;
  POST: ApiMethod<P, 'post'>;
  PATCH: ApiMethod<P, 'patch'>;
  DELETE: ApiMethod<P, 'delete'>;
  /**
   * Register openapi-fetch middleware (auth, refresh, ...). `onResponse` sees non-2xx responses.
   * Network failures reach `onError` middleware already wrapped as `ApiError` (`network_error`).
   */
  use(...middleware: Middleware[]): void;
  /** Unregister middleware. */
  eject(...middleware: Middleware[]): void;
}

export interface ApiClientOptions {
  /**
   * Origin (plus any prefix) that operation paths are appended to. The generated paths already
   * start with `/api/v1`, so pass the origin, for example `https://api.example.com`.
   */
  baseUrl: string;
  /** Replaces the platform `fetch` (tests, custom transports). Resolved lazily when omitted. */
  fetch?: typeof globalThis.fetch;
  /** Headers sent with every request. */
  headers?: ClientOptions['headers'];
  /**
   * Supplies the access token (and, in body mode, the refresh token). Without it the client sends
   * no Authorization header and never refreshes.
   */
  tokenStore?: TokenStore;
  /** Called once when a refresh is rejected, after the store is cleared. Errors it throws are swallowed. */
  onSessionExpired?: () => void;
}

type RawResult = { data?: unknown; error?: unknown; response: Response };
type RawCall = (url: string, init?: object) => Promise<RawResult>;

/**
 * Creates a typed API client. Successful calls resolve to the response data. Failures throw
 * {@link ApiError}: an error envelope, an unexpected non-2xx response, or a network failure.
 * An `AbortError` is rethrown unchanged so callers can cancel requests. After a response arrived,
 * a `SyntaxError` (body is not JSON) becomes `unexpected_response` with the real status, and a
 * `TypeError` (body read failed) becomes `network_error` with status 0; anything else, such as an
 * `onResponse` middleware's own error, is rethrown unchanged.
 */
export function createApiClient<P extends object = paths>(options: ApiClientOptions): ApiClient<P> {
  const safeFetch = async (input: Request, perCall?: typeof globalThis.fetch) => {
    try {
      return await (perCall ?? options.fetch ?? globalThis.fetch)(input);
    } catch (error) {
      if (isAbortError(error)) throw error;
      throw new ApiError({
        status: 0,
        code: NETWORK_ERROR,
        message: error instanceof Error && error.message ? error.message : 'Network request failed',
        cause: error,
      });
    }
  };

  const inner = createClient<P>({
    baseUrl: options.baseUrl,
    headers: options.headers,
    fetch: (input: Request) => safeFetch(input),
  });

  // Registered first: its onRequest runs before user middleware. openapi-fetch runs onResponse in
  // reverse order, so the auth hook runs LAST: user onResponse sees the pre-retry 401, and the
  // retried response passes through no middleware.
  inner.use(
    createAuthMiddleware({
      baseUrl: options.baseUrl,
      tokenStore: options.tokenStore,
      onSessionExpired: options.onSessionExpired,
    }),
  );

  // The public ApiClient interface carries the strict typings; the bodies stay loosely typed.
  const method = (name: 'GET' | 'PUT' | 'POST' | 'PATCH' | 'DELETE') => {
    return async (url: string, init?: object) => {
      const call = inner[name] as unknown as RawCall;
      // Remember the response so a body that fails to parse can still report the real status.
      let seen: Response | undefined;
      // A per-call `fetch` in init is honoured: it replaces the base fetch for this call only.
      const perCall = (init as { fetch?: typeof globalThis.fetch } | undefined)?.fetch;
      const trackingFetch = async (input: Request): Promise<Response> => {
        seen = await safeFetch(input, perCall);
        return seen;
      };
      let result: RawResult;
      try {
        result = await call(url, { ...init, fetch: trackingFetch });
      } catch (error) {
        if (seen && error instanceof SyntaxError) {
          // A 2xx whose body is not the JSON the spec promises (for example an SPA index.html).
          throw new ApiError({
            status: seen.status,
            code: UNEXPECTED_RESPONSE,
            message: 'The response body could not be parsed.',
            requestId: seen.headers.get('X-Request-ID') || null,
            cause: error,
          });
        }
        if (seen && error instanceof TypeError) {
          // The connection dropped while reading the body: no usable response, so status 0.
          throw new ApiError({
            status: 0,
            code: NETWORK_ERROR,
            message: error.message || 'Network request failed',
            requestId: seen.headers.get('X-Request-ID') || null,
            cause: error,
          });
        }
        throw error;
      }
      if (!result.response.ok) {
        throw apiErrorFromResponse(result.response, result.error);
      }
      return result.data;
    };
  };

  return {
    GET: method('GET'),
    PUT: method('PUT'),
    POST: method('POST'),
    PATCH: method('PATCH'),
    DELETE: method('DELETE'),
    use: (...middleware: Middleware[]) => inner.use(...middleware),
    eject: (...middleware: Middleware[]) => inner.eject(...middleware),
  } as unknown as ApiClient<P>;
}
