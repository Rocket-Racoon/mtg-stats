/** Structured extra information of an error envelope (for example `{ fields: [...] }`). */
export type ApiErrorDetails = Record<string, unknown>;

export interface ApiErrorInit {
  status: number;
  code: string;
  message: string;
  details?: ApiErrorDetails | null;
  requestId?: string | null;
  /** Seconds to wait before retrying, from the `Retry-After` header. */
  retryAfter?: number | null;
  cause?: unknown;
}

/** Code used when a non-2xx response does not carry a valid error envelope. */
export const UNEXPECTED_RESPONSE = 'unexpected_response';
/** Code used when the request never produced a response (`status` is 0). */
export const NETWORK_ERROR = 'network_error';

/**
 * Every failed API call throws this. Branch on `code`, never on `message`.
 * `status` is the HTTP status, or 0 when no response was received.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: ApiErrorDetails | null;
  readonly requestId: string | null;
  /** Seconds from the `Retry-After` header (usually on a 429), or null when absent or invalid. */
  readonly retryAfter: number | null;

  constructor(init: ApiErrorInit) {
    super(init.message, init.cause === undefined ? undefined : { cause: init.cause });
    this.name = 'ApiError';
    this.status = init.status;
    this.code = init.code;
    this.details = init.details ?? null;
    this.requestId = init.requestId ?? null;
    this.retryAfter = init.retryAfter ?? null;
    if (init.cause !== undefined && this.cause !== init.cause) {
      // Hermes may ignore the Error options bag; assign it explicitly.
      Object.defineProperty(this, 'cause', {
        value: init.cause,
        writable: true,
        configurable: true,
        enumerable: false,
      });
    }
    // Keep `instanceof ApiError` working even if the class is down-levelled to ES5.
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/**
 * Parses a `Retry-After` header: delta-seconds (a non-negative integer) or an HTTP date, measured
 * from now and clamped at 0 (rounded up to whole seconds). Anything else gives null.
 */
export function parseRetryAfter(
  value: string | null | undefined,
  now: number = Date.now(),
): number | null {
  if (value === null || value === undefined) return null;
  const text = value.trim();
  if (/^\d+$/.test(text)) return Number(text);
  // A date always has letters (day/month names); this rejects "-5", "1.5" and "".
  if (!/[A-Za-z]/.test(text)) return null;
  const at = Date.parse(text);
  if (Number.isNaN(at)) return null;
  return Math.max(0, Math.ceil((at - now) / 1000));
}

/**
 * Builds the ApiError for a non-2xx response. `body` is the parsed JSON body, or the raw
 * text when it was not JSON.
 */
export function apiErrorFromResponse(response: Response, body: unknown): ApiError {
  const headerRequestId = response.headers.get('X-Request-ID');
  const retryAfter = parseRetryAfter(response.headers.get('Retry-After'));
  const envelope = isRecord(body) ? body.error : undefined;
  if (
    isRecord(envelope) &&
    typeof envelope.code === 'string' &&
    typeof envelope.message === 'string'
  ) {
    return new ApiError({
      status: response.status,
      code: envelope.code,
      message: envelope.message,
      details: isRecord(envelope.details) ? envelope.details : null,
      requestId:
        typeof envelope.request_id === 'string' && envelope.request_id !== ''
          ? envelope.request_id
          : headerRequestId,
      retryAfter,
    });
  }
  return new ApiError({
    status: response.status,
    code: UNEXPECTED_RESPONSE,
    message: response.statusText || `Unexpected response (HTTP ${response.status})`,
    details: isRecord(body) ? body : null,
    requestId: headerRequestId,
    retryAfter,
  });
}

export function isAbortError(error: unknown): boolean {
  return (
    typeof error === 'object' &&
    error !== null &&
    (error as { name?: unknown }).name === 'AbortError'
  );
}
