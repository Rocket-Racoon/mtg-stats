// S-2.1.7 AC3 / decision 7: parseInviteToken accepts a pasted invite link or a bare token.
import { parseInviteToken } from '../session/parseInviteToken';

describe('parseInviteToken: accepted inputs', () => {
  it.each([
    ['an https link', 'https://mtgstats.example/invite/abc123', 'abc123'],
    ['an http link', 'http://mtgstats.example/invite/abc123', 'abc123'],
    ['an https link with a port', 'https://mtgstats.example:8443/invite/abc123', 'abc123'],
    ['an mtgstats:// link', 'mtgstats://invite/abc123', 'abc123'],
    ['a link with a trailing slash', 'https://mtgstats.example/invite/abc123/', 'abc123'],
    ['a link with a query', 'https://mtgstats.example/invite/abc123?utm=1&x=2', 'abc123'],
    ['a link with a hash', 'https://mtgstats.example/invite/abc123#top', 'abc123'],
    ['a link with a query and a hash', 'https://mtgstats.example/invite/abc123?a=1#b', 'abc123'],
    ['an mtgstats:// link with a query', 'mtgstats://invite/abc123?x=1', 'abc123'],
    ['a percent-encoded segment', 'https://mtgstats.example/invite/a%2Db%5Fc', 'a-b_c'],
    [
      'surrounding whitespace on a link',
      '  \n https://mtgstats.example/invite/abc123 \t',
      'abc123',
    ],
    ['a token with - and _', 'Ab-Cd_09', 'Ab-Cd_09'],
    ['a bare token', 'abc123', 'abc123'],
    ['a bare token with surrounding whitespace', '  abc123  ', 'abc123'],
    ['a one-character bare token', 'a', 'a'],
    ['a bare token of exactly 128 characters', 'a'.repeat(128), 'a'.repeat(128)],
  ])('extracts the token from %s', (_label, input, expected) => {
    expect(parseInviteToken(input)).toBe(expected);
  });
});

describe('parseInviteToken: rejected inputs', () => {
  it.each([
    ['an empty string', ''],
    ['only whitespace', '   \n '],
    ['a reset link', 'https://mtgstats.example/reset/abc123'],
    ['an mtgstats reset link', 'mtgstats://reset/abc123'],
    ['an invite link with no token', 'https://mtgstats.example/invite/'],
    ['an invite link with no token segment at all', 'https://mtgstats.example/invite'],
    ['an invite link whose token is only a query', 'https://mtgstats.example/invite/?t=abc'],
    ['an invite path that is not the first segment', 'https://mtgstats.example/a/invite/abc123'],
    ['a bare token with a space inside', 'abc 123'],
    ['a bare token with illegal characters', 'abc!123'],
    ['a bare token with a slash', 'abc/123'],
    ['a bare token over 128 characters', 'a'.repeat(129)],
    ['junk text', 'not a link, just words'],
    ['a javascript: URL', 'javascript:alert(1)'],
    ['a bare scheme', 'https://'],
  ])('returns null for %s', (_label, input) => {
    expect(parseInviteToken(input)).toBeNull();
  });
});
