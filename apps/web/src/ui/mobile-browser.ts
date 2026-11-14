/** The parts of `navigator` that mobile detection reads; tests pass fakes. */
export interface NavigatorLike {
  userAgent?: string;
  platform?: string;
  maxTouchPoints?: number;
  userAgentData?: { mobile?: boolean };
}

const MOBILE_UA = /Android|iPhone|iPad|iPod/i;

/**
 * True on a mobile browser: the UA client hint says mobile, the user agent names Android or an
 * iOS device, or it is iPadOS in desktop mode (reports MacIntel but has a touch screen).
 */
export function isMobileBrowser(
  nav: NavigatorLike | undefined = typeof navigator === 'undefined' ? undefined : navigator,
): boolean {
  if (!nav) return false;
  if (nav.userAgentData?.mobile === true) return true;
  if (MOBILE_UA.test(nav.userAgent ?? '')) return true;
  return nav.platform === 'MacIntel' && (nav.maxTouchPoints ?? 0) > 1;
}
