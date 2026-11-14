/**
 * The parts of `navigator` that Android detection reads; tests pass fakes. `platform` and
 * `maxTouchPoints` aren't read today; they stay for iOS detection once there's an iOS app.
 */
export interface NavigatorLike {
  userAgent?: string;
  platform?: string;
  maxTouchPoints?: number;
  userAgentData?: { platform?: string; mobile?: boolean };
}

const ANDROID_UA = /Android/i;

/**
 * True on an Android browser: the UA client hint platform is Android, or the user agent names
 * Android. `userAgentData.mobile` is deliberately not used (an Android tablet may report false, a
 * non-Android phone may report true). iPhone, iPad, iPadOS desktop mode and desktops are false.
 */
export function isAndroidBrowser(
  nav: NavigatorLike | undefined = typeof navigator === 'undefined' ? undefined : navigator,
): boolean {
  if (!nav) return false;
  if (nav.userAgentData?.platform === 'Android') return true;
  return ANDROID_UA.test(nav.userAgent ?? '');
}
