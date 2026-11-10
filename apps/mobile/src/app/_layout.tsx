import { Stack } from 'expo-router';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { SessionProvider, useSession } from '../session/SessionProvider';

export function RootNavigator() {
  const { status } = useSession();
  const signedIn = status === 'signed-in';
  return (
    <Stack screenOptions={{ headerShown: false }}>
      <Stack.Protected guard={!signedIn}>
        <Stack.Screen name="(auth)" />
      </Stack.Protected>
      <Stack.Protected guard={signedIn}>
        <Stack.Screen name="(tabs)" />
      </Stack.Protected>
      {/* Deep-link targets: reachable signed in or out for now (F-2.1 / F-6.2 decide guards later). */}
      <Stack.Screen name="join/[code]" options={{ headerShown: true }} />
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
      <SessionProvider>
        <RootNavigator />
      </SessionProvider>
    </GestureHandlerRootView>
  );
}
