import createClient, {
  type Client,
  type ClientOptions,
  type ClientPathsWithMethod,
  type FetchResponse,
  type MaybeOptionalInit,
  type Middleware,
} from 'openapi-fetch';
import { ApiError, NETWORK_ERROR, apiErrorFromResponse, isAbortError } from './errors';
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
  /** Register openapi-fetch middleware (auth, refresh, ...). `onResponse` sees non-2xx responses. */
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
}

type RawResult = { data?: unknown; error?: unknown; response: Response };
type RawCall = (url: string, init?: object) => Promise<RawResult>;

/**
 * Creates a typed API client. Successful calls resolve to the response data. Failures throw
 * {@link ApiError}: an error envelope, an unexpected non-2xx response, or a network failure.
 * An `AbortError` is rethrown unchanged so callers can cancel requests.
 */
export function createApiClient<P extends object = paths>(options: ApiClientOptions): ApiClient<P> {
  const baseFetch = options.fetch;
  const safeFetch = async (input: Request): Promise<Response> => {
    try {
      return await (baseFetch ?? globalThis.fetch)(input);
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
    fetch: safeFetch,
  });

  // The public ApiClient interface carries the strict typings; the bodies stay loosely typed.
  const method = (name: 'GET' | 'PUT' | 'POST' | 'PATCH' | 'DELETE') => {
    return async (url: string, init?: object) => {
      const call = inner[name] as unknown as RawCall;
      const result = await call(url, init);
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
