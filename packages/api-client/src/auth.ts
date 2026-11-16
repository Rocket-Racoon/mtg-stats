import type { Middleware } from 'openapi-fetch';
import { ApiError, NETWORK_ERROR, apiErrorFromResponse } from './errors';

/** The refresh endpoint. Exposed so apps and tests can match it. */
export const REFRESH_PATH = '/api/v1/auth/refresh';
const LOGIN_PATH = '/api/v1/auth/login';
const LOGOUT_PATH = '/api/v1/auth/logout';
const AUTH_PREFIX = '/api/v1/auth/';
const REFRESH_LOCK = 'mtgstats-auth-refresh';

export interface AuthTokens {
  accessToken: string;
  /** Present only in body mode (the refresh response carried it). */
  refreshToken?: string;
}

export interface TokenStore {
  getAccessToken(): string | null | Promise<string | null>;
  /**
   * Body mode (mobile): return the stored refresh token. Omit the method, or return null/'',
   * for cookie mode (web: the refresh token is the HttpOnly cookie).
   */
  getRefreshToken?(): string | null | Promise<string | null>;
  /** Called after every successful refresh, before the original request is retried. */
  setTokens(tokens: AuthTokens): void | Promise<void>;
  /** Called when the refresh is rejected, before onSessionExpired. */
  clear(): void | Promise<void>;
}

type FetchFn = (input: Request) => Promise<Response>;

/** The slice of the Web Locks API used here. Typed locally: React Native has no DOM lib. */
interface LockManagerLike {
  request<T>(name: string, callback: () => Promise<T>): Promise<T>;
}

type RefreshOutcome =
  { kind: 'ok'; accessToken: string } | { kind: 'rejected' } | { kind: 'transient'; error: Error };

interface Pending {
  /** Clone of the request taken before it was sent, so the body can be resent. */
  clone: Request;
  /** The access token this request was sent with. */
  sentToken: string | null;
  /** The caller supplied its own Authorization header: never refresh or retry. */
  callerAuth: boolean;
}

export interface AuthMiddlewareOptions {
  baseUrl: string;
  tokenStore?: TokenStore;
  onSessionExpired?: () => void;
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && value !== '';
}

function bearerToken(request: Request): string | null {
  const header = request.headers.get('Authorization');
  if (header === null) return null;
  return header.replace(/^Bearer\s+/i, '') || null;
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return JSON.parse(await response.text());
  } catch {
    return undefined;
  }
}

/** Releases the original 401 body before a retry. Never throws. */
function discard(response: Response): void {
  try {
    const body = (response as { body?: { cancel?: () => Promise<void> } | null }).body;
    void body?.cancel?.()?.catch(() => undefined);
  } catch {
    // Ignore: the body may be locked or already used.
  }
}

/** The caller aborted while the refresh ran: surface an AbortError, not a 401 or refresh error. */
function throwIfAborted(request: Request): void {
  const signal = request.signal as AbortSignal | undefined;
  if (!signal?.aborted) return;
  const reason: unknown = signal.reason;
  if (reason instanceof Error && reason.name === 'AbortError') throw reason;
  const Ctor = (globalThis as { DOMException?: new (m: string, n: string) => Error }).DOMException;
  if (Ctor) throw new Ctor('The operation was aborted.', 'AbortError');
  const error = new Error('The operation was aborted.');
  error.name = 'AbortError';
  throw error;
}

function toApiError(error: unknown): Error {
  if (error instanceof ApiError) return error;
  return new ApiError({
    status: 0,
    code: NETWORK_ERROR,
    message: error instanceof Error && error.message ? error.message : 'Network request failed',
    cause: error,
  });
}

/** The auth middleware plus `refresh`, the same single-flight refresh the 401 retry uses. */
export type AuthMiddleware = Middleware & {
  /**
   * Refreshes the session now (cookie or body mode, as the token store dictates). Resolves true
   * after `setTokens`, false when the refresh was rejected (the store is cleared and
   * `onSessionExpired` has run) or when there is no token store; throws the ApiError when the
   * failure was transient.
   */
  refresh(fetchFn: FetchFn): Promise<boolean>;
};

/**
 * The auth middleware: sets the Bearer header, sends cookies to `/api/v1/auth/*`, and on a 401
 * refreshes once (single-flight per client) and retries the original request once.
 * Must be registered before any other middleware.
 */
export function createAuthMiddleware(options: AuthMiddlewareOptions): AuthMiddleware {
  const { tokenStore, onSessionExpired } = options;
  const baseUrl = options.baseUrl.replace(/\/+$/, '');
  // Keyed by the per-call `options` object openapi-fetch passes to every hook of one call.
  const pending = new WeakMap<object, Pending>();
  let inFlight: Promise<RefreshOutcome> | null = null;

  const performRefresh = async (fetchFn: FetchFn): Promise<RefreshOutcome> => {
    if (!tokenStore) return { kind: 'rejected' };
    const stored = tokenStore.getRefreshToken ? await tokenStore.getRefreshToken() : null;
    const refreshToken = isNonEmptyString(stored) ? stored : null;
    const bodyMode = refreshToken !== null;

    const call = async (): Promise<RefreshOutcome> => {
      const request = bodyMode
        ? new Request(baseUrl + REFRESH_PATH, {
            method: 'POST',
            credentials: 'include',
            headers: { 'X-Client': 'mobile', 'Content-Type': 'application/json' },
            body: JSON.stringify({ refresh_token: refreshToken }),
          })
        : new Request(baseUrl + REFRESH_PATH, { method: 'POST', credentials: 'include' });

      let response: Response;
      try {
        response = await fetchFn(request);
      } catch (error) {
        return { kind: 'transient', error: toApiError(error) };
      }

      const status = response.status;
      const body = await readJson(response);
      if (status < 200 || status >= 300) {
        const rejection = status >= 400 && status < 500 && status !== 408 && status !== 429;
        return rejection
          ? { kind: 'rejected' }
          : { kind: 'transient', error: apiErrorFromResponse(response, body) };
      }

      const record =
        typeof body === 'object' && body !== null ? (body as Record<string, unknown>) : {};
      const accessToken = record.access_token;
      const newRefresh = record.refresh_token;
      if (!isNonEmptyString(accessToken) || (bodyMode && !isNonEmptyString(newRefresh))) {
        return { kind: 'rejected' };
      }
      await tokenStore.setTokens(
        bodyMode ? { accessToken, refreshToken: newRefresh as string } : { accessToken },
      );
      return { kind: 'ok', accessToken };
    };

    const locks = (globalThis as { navigator?: { locks?: LockManagerLike } }).navigator?.locks;
    if (!bodyMode && locks && typeof locks.request === 'function') {
      return locks.request(REFRESH_LOCK, call);
    }
    return call();
  };

  const refreshOnce = (fetchFn: FetchFn): Promise<RefreshOutcome> => {
    if (inFlight) return inFlight;
    const run = (async (): Promise<RefreshOutcome> => {
      const outcome = await performRefresh(fetchFn);
      if (outcome.kind === 'rejected' && tokenStore) {
        try {
          await tokenStore.clear();
        } catch {
          // Best effort: the session is over either way.
        }
        try {
          onSessionExpired?.();
        } catch {
          // A throwing callback must not break the waiting requests.
        }
      }
      return outcome;
    })();
    inFlight = run;
    const release = () => {
      if (inFlight === run) inFlight = null;
    };
    run.then(release, release);
    return run;
  };

  const retryRequest = (entry: Pending, request: Request, token: string): Request => {
    // Headers come from the final request (user middleware may have added some); the body from the clone.
    const headers = new Headers(request.headers);
    headers.set('Authorization', `Bearer ${token}`);
    return new Request(entry.clone, { headers });
  };

  return {
    async refresh(fetchFn) {
      if (!tokenStore) return false;
      const outcome = await refreshOnce(fetchFn);
      if (outcome.kind === 'transient') throw outcome.error;
      return outcome.kind === 'ok';
    },

    async onRequest({ request, schemaPath, options: callOptions }) {
      let current = request;
      if (schemaPath.startsWith(AUTH_PREFIX) && current.credentials === 'same-origin') {
        // `same-origin` is the Request default, so an explicit caller choice survives.
        current = new Request(current, { credentials: 'include' });
      }
      if (!tokenStore) return current === request ? undefined : current;

      const callerAuth = current.headers.has('Authorization');
      if (!callerAuth) {
        const token = await tokenStore.getAccessToken();
        if (isNonEmptyString(token)) current.headers.set('Authorization', `Bearer ${token}`);
      }
      pending.set(callOptions, {
        clone: current.clone(),
        sentToken: bearerToken(current),
        callerAuth,
      });
      return current === request ? undefined : current;
    },

    async onResponse({ request, response, schemaPath, options: callOptions }) {
      const entry = pending.get(callOptions);
      pending.delete(callOptions);
      if (
        !tokenStore ||
        !entry ||
        entry.callerAuth ||
        response.status !== 401 ||
        schemaPath === LOGIN_PATH ||
        schemaPath === REFRESH_PATH ||
        schemaPath === LOGOUT_PATH
      ) {
        return undefined;
      }
      const fetchFn = callOptions.fetch as FetchFn;

      // Another request already refreshed: retry with the current token, no new refresh.
      const latest = await tokenStore.getAccessToken();
      if (isNonEmptyString(latest) && latest !== entry.sentToken) {
        discard(response);
        return fetchFn(retryRequest(entry, request, latest));
      }

      const outcome = await refreshOnce(fetchFn);
      throwIfAborted(request);
      if (outcome.kind === 'rejected') return undefined; // the original 401 becomes the ApiError
      if (outcome.kind === 'transient') throw outcome.error;
      discard(response);
      return fetchFn(retryRequest(entry, request, outcome.accessToken));
    },

    onError({ options: callOptions }) {
      pending.delete(callOptions);
      return undefined;
    },
  };
}
