import { isMobileBrowser, type NavigatorLike } from './mobile-browser';

/** The mobile app's URL scheme (apps/mobile app.json; routes handled per S-1.7.2). */
export const APP_SCHEME = 'mtgstats';

export const LINK_KINDS = ['invite', 'reset', 'join'] as const;
export type LinkKind = (typeof LINK_KINDS)[number];

/** The in-app deep link for a shareable page. Never an https link: those come from the API. */
export function appLink(kind: LinkKind, value: string): string {
  return `${APP_SCHEME}://${kind}/${encodeURIComponent(value)}`;
}

/**
 * "Open in app" link for the public invite, reset and join pages (S-1.6.7). It renders only on a
 * mobile browser (nothing at all on desktop, so there is no dead link for keyboard or screen
 * reader users) and never for an empty value. The page works fully without it.
 */
export function OpenInAppButton({
  kind,
  value,
  nav,
}: {
  kind: LinkKind;
  value: string;
  /** Test seam: defaults to the real `navigator`. */
  nav?: NavigatorLike;
}) {
  if (value === '' || !isMobileBrowser(nav)) return null;
  return (
    <a className="open-in-app" href={appLink(kind, value)}>
      Open in app
    </a>
  );
}
