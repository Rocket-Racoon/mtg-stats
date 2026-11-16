// Unit tests for the small pure helpers behind the sign-in and invite pages (S-2.1.6).
import { describe, expect, it } from 'vitest';
import { ApiError } from '@mtg-stats/api-client';
import { fieldErrorsFromDetails } from '../pages/auth/errors';
import { TOO_MANY_ATTEMPTS_FALLBACK, tooManyAttemptsMessage } from './rate-limit';
import {
  validateDisplayName,
  validateEmail,
  validatePassword,
  validateUsername,
} from './validation';

describe('tooManyAttemptsMessage', () => {
  it.each([
    [0, 'Too many attempts. Try again in 1 minute.'],
    [30, 'Too many attempts. Try again in 1 minute.'],
    [60, 'Too many attempts. Try again in 1 minute.'],
    [61, 'Too many attempts. Try again in 2 minutes.'],
    [120, 'Too many attempts. Try again in 2 minutes.'],
  ])('formats %s seconds', (seconds, expected) => {
    expect(tooManyAttemptsMessage(seconds)).toBe(expected);
  });

  it('falls back when there is no Retry-After', () => {
    expect(tooManyAttemptsMessage(null)).toBe(TOO_MANY_ATTEMPTS_FALLBACK);
  });
});

describe('validation', () => {
  it('checks the username bounds and characters', () => {
    expect(validateUsername('ab')).not.toBeNull();
    expect(validateUsername('a'.repeat(25))).not.toBeNull();
    expect(validateUsername('bad name')).not.toBeNull();
    expect(validateUsername('ada_l.o-ve')).toBeNull();
  });

  it('checks the password bounds', () => {
    expect(validatePassword('x'.repeat(11))).not.toBeNull();
    expect(validatePassword('x'.repeat(12))).toBeNull();
    expect(validatePassword('x'.repeat(129))).not.toBeNull();
  });

  it('trims the display name before measuring', () => {
    expect(validateDisplayName('   ')).not.toBeNull();
    expect(validateDisplayName(' Ada ')).toBeNull();
    expect(validateDisplayName('x'.repeat(33))).not.toBeNull();
  });

  it('allows an empty email and caps it at 320', () => {
    expect(validateEmail('')).toBeNull();
    expect(validateEmail('x'.repeat(321))).not.toBeNull();
  });
});

describe('fieldErrorsFromDetails', () => {
  it('maps the last loc element to the field name', () => {
    const error = new ApiError({
      status: 422,
      code: 'validation_error',
      message: 'Request validation failed.',
      details: {
        fields: [
          { loc: ['body', 'username'], path: 'body.username', message: 'Too short.' },
          { loc: ['body', 'password'], path: 'body.password', message: 'Too weak.' },
          { loc: ['body', 0], path: 'body.0', message: 'ignored' },
        ],
      },
    });
    expect(fieldErrorsFromDetails(error)).toEqual({
      username: 'Too short.',
      password: 'Too weak.',
    });
  });
});
