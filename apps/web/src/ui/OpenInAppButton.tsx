import { isAndroidBrowser, type NavigatorLike } from './android-browser';

/** The mobile app's URL scheme (apps/mobile app.json; routes handled per S-1.7.2). */
export const APP_SCHEME = 'mtgstats';

export const LINK_KINDS = ['invite', 'reset', 'join'] as const;
export type LinkKind = (typeof LINK_KINDS)[number];

/** The in-app deep link for a shareable page. Never an https link: those come from the API. */
export function appLink(kind: LinkKind, value: string): string {
  return `${APP_SCHEME}://${kind}/${encodeURIComponent(value)}`;
}

/**
 * "Open in app" link for the public invite, reset and join pages (S-1.6.7). It renders only on an
 * Android browser: the native app is Android-only for now (architecture.md section 5), and on iOS
 * Safari without the app an mtgstats:// link shows an "invalid address" error. Nothing renders on
 * iOS or desktop, so there is no dead link for keyboard or screen reader users, and never for an
 * empty value. The page works fully without it. Universal/App Links replace this in Phase 5
 * (S-11.5.6), and iOS can be added once there is an iOS app.
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
  if (value === '' || !isAndroidBrowser(nav)) return null;
  return (
    <a className="open-in-app" href={appLink(kind, value)}>
      Open in app
    </a>
  );
}
