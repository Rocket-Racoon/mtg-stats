import { ApiError, NETWORK_ERROR } from '@mtg-stats/api-client';

export const GENERIC_ERROR_MESSAGE = 'Something went wrong. Please try again.';
export const NETWORK_ERROR_MESSAGE = "Can't reach the server. Check your connection and try again.";

/** A user-safe sentence for any error: the envelope message for ApiError, else a fixed sentence. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    // A network error's message is the browser's raw text ("Failed to fetch", "Load failed").
    if (error.code === NETWORK_ERROR) return NETWORK_ERROR_MESSAGE;
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
