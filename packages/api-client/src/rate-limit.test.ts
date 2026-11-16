// Unit tests for the shared 429 message (S-2.1.6 AC2, reused by mobile in S-2.1.7).
import { describe, expect, it } from 'vitest';
import { TOO_MANY_ATTEMPTS_FALLBACK, tooManyAttemptsMessage } from './rate-limit';

describe('tooManyAttemptsMessage', () => {
  it.each([
    [0, 'Too many attempts. Try again in 1 minute.'],
    [30, 'Too many attempts. Try again in 1 minute.'],
    [60, 'Too many attempts. Try again in 1 minute.'],
    [61, 'Too many attempts. Try again in 2 minutes.'],
    [120, 'Too many attempts. Try again in 2 minutes.'],
    [3600, 'Too many attempts. Try again in 60 minutes.'],
  ])('formats %s seconds', (seconds, expected) => {
    expect(tooManyAttemptsMessage(seconds)).toBe(expected);
  });

  it('falls back when there is no Retry-After', () => {
    expect(tooManyAttemptsMessage(null)).toBe(TOO_MANY_ATTEMPTS_FALLBACK);
  });

  it('uses the exact fallback text', () => {
    expect(TOO_MANY_ATTEMPTS_FALLBACK).toBe('Too many attempts. Try again later.');
  });
});
