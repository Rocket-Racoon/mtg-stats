const TOKEN_PATTERN = /^[A-Za-z0-9_-]{1,128}$/;
const URL_PATTERN = /^(https?|mtgstats):\/\/([^/?#]*)([^?#]*)/i;

/**
 * Extracts an invite token from pasted text (S-2.1.7): a bare token, an `https://<host>/invite/<token>`
 * link (http too) or `mtgstats://invite/<token>`. Query and hash are ignored and the segment is
 * percent-decoded. Returns null for anything else.
 */
export function parseInviteToken(input: string): string | null {
  const text = input.trim();
  const match = URL_PATTERN.exec(text);
  if (!match) return TOKEN_PATTERN.test(text) ? text : null;
  const scheme = (match[1] as string).toLowerCase();
  const host = match[2] as string;
  const pathSegments = (match[3] as string).split('/').slice(1);
  // In mtgstats://invite/<token> the host position holds the route name.
  const segments = scheme === 'mtgstats' ? [host, ...pathSegments] : pathSegments;
  const [route, rawToken, ...rest] = segments;
  if (route !== 'invite' || !rawToken) return null;
  if (rest.length > 1 || (rest.length === 1 && rest[0] !== '')) return null; // only a trailing slash
  let token: string;
  try {
    token = decodeURIComponent(rawToken);
  } catch {
    return null;
  }
  return TOKEN_PATTERN.test(token) ? token : null;
}
