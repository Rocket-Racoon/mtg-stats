import { ApiError, NETWORK_ERROR, tooManyAttemptsMessage } from '@mtg-stats/api-client';

export const OFFLINE_MESSAGE = "You're offline";
export const SOMETHING_WENT_WRONG = 'Something went wrong. Try again.';
export const INVITE_INVALID_MESSAGE =
  'This invite has expired or was revoked. Ask your playgroup admin for a new one.';

export function isOffline(error: unknown): boolean {
  return error instanceof ApiError && error.code === NETWORK_ERROR;
}

export function isInviteGone(error: unknown): boolean {
  return (
    error instanceof ApiError && (error.status === 410 || error.code === 'invite_no_longer_valid')
  );
}

/** A form-level message for a failed auth call. Branches on status and `code`, never `message`. */
export function formErrorMessage(error: unknown): string {
  if (isOffline(error)) return OFFLINE_MESSAGE;
  if (error instanceof ApiError) {
    if (error.status === 429) return tooManyAttemptsMessage(error.retryAfter);
    if (error.status === 401) return error.message; // the envelope message, as on web
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
