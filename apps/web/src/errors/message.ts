import { ApiError } from '@mtg-stats/api-client';

export const GENERIC_ERROR_MESSAGE = 'Something went wrong. Please try again.';

/** A user-safe sentence for any error: the envelope message for ApiError, else a fixed sentence. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const message = error.message.trim();
    // Never show a body that looks like raw JSON.
    if (message !== '' && !/^[{[]/.test(message)) return message;
  }
  return GENERIC_ERROR_MESSAGE;
}

/** The request id to show for support, only for an ApiError that carries one. */
export function errorRequestId(error: unknown): string | null {
  return error instanceof ApiError && error.requestId ? error.requestId : null;
}
