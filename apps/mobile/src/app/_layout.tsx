import { Stack } from 'expo-router';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { AppProviders } from '../components/AppProviders';
import { usePendingLink } from '../session/pendingLink';
import { SessionProvider, useSession } from '../session/SessionProvider';
import { keepSplashVisible, useSplashWhileRestoring } from '../session/splash';

keepSplashVisible();

export function RootNavigator() {
  const { status } = useSession();
  useSplashWhileRestoring(status);
  usePendingLink(status);
  // While restoring, the native splash stays up and no screen mounts, so a signed-in relaunch
  // never flashes the sign-in stack and a deep link is resolved only once the status is known.
  if (status === 'restoring') return null;
  const signedIn = status === 'signed_in';
  return (
    <Stack screenOptions={{ headerShown: false }}>
      <Stack.Protected guard={!signedIn}>
        <Stack.Screen name="(auth)" />
      </Stack.Protected>
      <Stack.Protected guard={signedIn}>
        <Stack.Screen name="(tabs)" />
        <Stack.Screen name="join/[code]" options={{ headerShown: true }} />
      </Stack.Protected>
      {/* Public routes (session/publicRoutes.ts): reachable signed in or out. */}
      <Stack.Screen name="invite/[token]" options={{ headerShown: true }} />
      {/* Dev-only route: not registered in production builds. */}
      <Stack.Protected guard={__DEV__}>
        <Stack.Screen name="dev/gesture-test" options={{ headerShown: true }} />
      </Stack.Protected>
    </Stack>
  );
}

export default function RootLayout() {
  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <AppProviders>
        <SessionProvider>
          <RootNavigator />
        </SessionProvider>
      </AppProviders>
    </GestureHandlerRootView>
  );
}
