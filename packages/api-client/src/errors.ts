/** Structured extra information of an error envelope (for example `{ fields: [...] }`). */
export type ApiErrorDetails = Record<string, unknown>;

export interface ApiErrorInit {
  status: number;
  code: string;
  message: string;
  details?: ApiErrorDetails | null;
  requestId?: string | null;
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

  constructor(init: ApiErrorInit) {
    super(init.message, init.cause === undefined ? undefined : { cause: init.cause });
    this.name = 'ApiError';
    this.status = init.status;
    this.code = init.code;
    this.details = init.details ?? null;
    this.requestId = init.requestId ?? null;
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
 * Builds the ApiError for a non-2xx response. `body` is the parsed JSON body, or the raw
 * text when it was not JSON.
 */
export function apiErrorFromResponse(response: Response, body: unknown): ApiError {
  const headerRequestId = response.headers.get('X-Request-ID');
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
    });
  }
  return new ApiError({
    status: response.status,
    code: UNEXPECTED_RESPONSE,
    message: response.statusText || `Unexpected response (HTTP ${response.status})`,
    details: isRecord(body) ? body : null,
    requestId: headerRequestId,
  });
}

export function isAbortError(error: unknown): boolean {
  return (
    typeof error === 'object' &&
    error !== null &&
    (error as { name?: unknown }).name === 'AbortError'
  );
}
