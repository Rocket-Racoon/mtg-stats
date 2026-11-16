import { router, useGlobalSearchParams, usePathname, useSegments, type Href } from 'expo-router';
import { useEffect, useRef } from 'react';
import { isProtectedRoot, isPublicLinkRoot } from './publicRoutes';
import type { SessionStatus } from './SessionProvider';

type Params = Record<string, string | string[] | undefined>;

/**
 * Builds a string Href from the route pattern (`/join/[code]`) and the decoded params: dynamic
 * segments are re-encoded (so an encoded slash stays one segment) and the remaining params go in
 * the query string with repeated keys kept (`?x=1&x=2`); object hrefs would join arrays.
 */
function toHref(segments: string[], params: Params): Href {
  const used = new Set<string>();
  const path = segments.map((segment) => {
    const match = /^\[(\.\.\.)?(.+)\]$/.exec(segment);
    if (!match?.[2]) return segment;
    used.add(match[2]);
    return [params[match[2]] ?? ''].flat().map(encodeURIComponent).join('/');
  });
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (used.has(key) || value === undefined) continue;
    for (const v of [value].flat()) query.append(key, v);
  }
  const qs = query.toString();
  return `/${path.join('/')}${qs ? `?${qs}` : ''}` as Href;
}

/**
 * Remembers (in memory only) a protected route opened while signed out and opens it once when
 * the status becomes `signed_in`. Signing out, or a start that is not signed out, never applies it.
 */
export function usePendingLink(status: SessionStatus): void {
  const pathname = usePathname();
  const segments = useSegments() as string[];
  const params = useGlobalSearchParams() as Params;
  const pending = useRef<Href | null>(null);
  const previous = useRef(status);
  const paramsKey = JSON.stringify(params);

  useEffect(() => {
    const prev = previous.current;
    previous.current = status;
    const root = segments[0] ?? '';
    if (status === 'signed_out') {
      if (prev === 'signed_in') {
        // A real sign-out: its first render still reports the old protected path. Not a link.
        pending.current = null;
      } else if (isPublicLinkRoot(root)) {
        pending.current = null; // an Invite or Reset link supersedes an earlier protected one
      } else if (isProtectedRoot(root)) {
        pending.current = toHref(segments, params);
      }
    } else if (status === 'restoring') {
      // `restoring` is a cold-start-only state (E-2); entering it drops any pending link.
      pending.current = null;
    } else if (pending.current && isProtectedRoot(root)) {
      // Signed in and the tabs are showing: open the remembered screen over them, once.
      const href = pending.current;
      pending.current = null;
      router.navigate(href);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- params is tracked through paramsKey
  }, [status, pathname, paramsKey]);
}
