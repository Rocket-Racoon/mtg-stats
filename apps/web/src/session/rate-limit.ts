/** Shown when a 429 carries no usable `Retry-After`. */
export const TOO_MANY_ATTEMPTS_FALLBACK = 'Too many attempts. Try again later.';

/**
 * "Too many attempts. Try again in N minutes" for a 429 (S-2.1.6 AC2). `retryAfterSeconds` is
 * `ApiError.retryAfter`; N is rounded up to whole minutes and is at least 1 ("1 minute").
 * A null `retryAfter` gives {@link TOO_MANY_ATTEMPTS_FALLBACK}.
 */
export function tooManyAttemptsMessage(retryAfterSeconds: number | null): string {
  if (retryAfterSeconds === null) return TOO_MANY_ATTEMPTS_FALLBACK;
  const minutes = Math.max(1, Math.ceil(retryAfterSeconds / 60));
  return `Too many attempts. Try again in ${minutes} ${minutes === 1 ? 'minute' : 'minutes'}.`;
}
