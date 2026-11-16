import * as SplashScreen from 'expo-splash-screen';
import { useEffect } from 'react';
import type { SessionStatus } from './store';

/** Keeps the native splash up until it is hidden. Call at module load of the root layout. */
export function preventSplashAutoHide(): void {
  try {
    // A rejection (for example no native splash registered) must not crash the launch.
    SplashScreen.preventAutoHideAsync().catch(() => undefined);
  } catch {
    // A synchronous throw is swallowed for the same reason.
  }
}

function hideSplash(): void {
  try {
    SplashScreen.hideAsync().catch(() => undefined);
  } catch {
    // Hiding an already hidden splash is harmless.
  }
}

/** Hides the splash once the session leaves `restoring`, so a relaunch never flashes sign-in. */
export function useSplashWhileRestoring(status: SessionStatus): void {
  const restoring = status === 'restoring';
  useEffect(() => {
    if (!restoring) hideSplash();
  }, [restoring]);
}

/** Hides the splash on mount, for screens shown outside the session (the config error screen). */
export function useHideSplashOnMount(): void {
  useEffect(() => {
    hideSplash();
  }, []);
}
