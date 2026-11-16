import * as SplashScreen from 'expo-splash-screen';
import { useEffect } from 'react';
import type { SessionStatus } from './SessionProvider';

/** Call once at module load of the root layout, before the first render. */
export function keepSplashVisible(): void {
  SplashScreen.preventAutoHideAsync().catch(() => {});
}

/** Hides the splash once the session leaves `restoring`. */
export function useSplashWhileRestoring(status: SessionStatus): void {
  useEffect(() => {
    if (status !== 'restoring') SplashScreen.hideAsync().catch(() => {});
  }, [status]);
}

/** For screens shown instead of the app (config error): the splash must never cover them. */
export function useHideSplashOnMount(): void {
  useEffect(() => {
    SplashScreen.hideAsync().catch(() => {});
  }, []);
}
