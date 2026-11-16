import { ApiError } from '@mtg-stats/api-client';
import { tooManyAttemptsMessage } from '../../session/rate-limit';

export const SOMETHING_WENT_WRONG = 'Something went wrong. Try again.';
export const INVITE_INVALID_MESSAGE =
  'This invite has expired or was revoked. Ask your playgroup admin for a new one.';

/** A form-level message for a failed auth call: 429 uses Retry-After, 401 the envelope message. */
export function formErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 429) return tooManyAttemptsMessage(error.retryAfter);
    if (error.status === 401) return error.message;
  }
  return SOMETHING_WENT_WRONG;
}

/** Maps a 422 `details.fields` to `{ fieldName: message }` using the last element of each `loc`. */
export function fieldErrorsFromDetails(error: ApiError): Record<string, string> {
  const result: Record<string, string> = {};
  const fields = error.details?.fields;
  if (!Array.isArray(fields)) return result;
  for (const item of fields as unknown[]) {
    if (typeof item !== 'object' || item === null) continue;
    const { loc, message } = item as { loc?: unknown; message?: unknown };
    if (!Array.isArray(loc) || typeof message !== 'string') continue;
    const last: unknown = loc[loc.length - 1];
    if (typeof last === 'string' && !(last in result)) result[last] = message;
  }
  return result;
}
