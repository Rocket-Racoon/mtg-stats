import { Stack, router, usePathname, type Href } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, StyleSheet, View } from 'react-native';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { api } from '../api';
import { AppProviders } from '../components/AppProviders';
import { isPendingCandidate } from '../session/paths';
import { restoreSession } from '../session/restore';
import { SessionProvider, useSession } from '../session/SessionProvider';
import { useSessionStore } from '../session/store';

export function RootNavigator() {
  const { status } = useSession();
  const pendingHref = useSessionStore((s) => s.pendingHref);
  const signedIn = status === 'signed-in';
  const restoring = status === 'restoring';

  // The launch URL, read on the first render while the navigator still holds it: once the session
  // settles, a protected path (for example mtgstats://decks) becomes the pending link. Signed out
  // it is kept for after sign-in; signed in it is replaced to below. The (tabs) guard stays shut
  // while restoring, so this is recorded here and not by opening the guard early. Only a launch
  // that starts in 'restoring' records anything.
  const pathname = usePathname();
  const [launch] = useState(() => ({ path: pathname, restoring }));
  const recorded = useRef(false);
  useEffect(() => {
    if (recorded.current || restoring) return;
    recorded.current = true;
    if (launch.restoring && isPendingCandidate(launch.path)) {
      useSessionStore.getState().setPendingHref(launch.path);
    }
  }, [restoring, launch]);

  // A protected link opened while signed out (see join/[code]) is taken once after sign-in. The
  // guard change (sign-in group out, tabs in) rewrites the navigation state when it renders, so
  // the replace waits one commit: `settled` flips in the commit after `signedIn` did.
  const [settled, setSettled] = useState(false);
  useEffect(() => {
    // Deliberate: the extra commit is the point (see the comment above).
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSettled(signedIn);
  }, [signedIn]);
  useEffect(() => {
    if (signedIn && settled && pendingHref !== null) {
      useSessionStore.getState().setPendingHref(null);
      router.replace(pendingHref as Href);
    }
  }, [signedIn, settled, pendingHref]);

  // The navigator stays mounted while restoring so it takes the launch URL (a deep link); the
  // loading view covers it until the restore settles.
  return (
    <View style={styles.root}>
      <View
        style={styles.root}
        importantForAccessibility={restoring ? 'no-hide-descendants' : 'auto'}
        accessibilityElementsHidden={restoring}
      >
        <Stack screenOptions={{ headerShown: false }}>
          <Stack.Protected guard={!signedIn}>
            <Stack.Screen name="(auth)" />
          </Stack.Protected>
          <Stack.Protected guard={signedIn}>
            <Stack.Screen name="(tabs)" />
          </Stack.Protected>
          {/* Reachable signed out: invite and reset links. */}
          <Stack.Screen name="invite/[token]" options={{ headerShown: true }} />
          <Stack.Screen name="reset/[token]" options={{ headerShown: true }} />
          {/* Signed in only; the screen itself redirects a signed-out visitor (it keeps the link). */}
          <Stack.Screen name="join/[code]" options={{ headerShown: true }} />
          {/* Dev-only route: not registered in production builds. */}
          <Stack.Protected guard={__DEV__}>
            <Stack.Screen name="dev/gesture-test" options={{ headerShown: true }} />
          </Stack.Protected>
        </Stack>
      </View>
      {restoring ? (
        <View style={styles.loading} testID="session-restoring">
          <ActivityIndicator accessibilityLabel="Loading" size="large" />
        </View>
      ) : null}
    </View>
  );
}

/** Restores the session once at launch (S-2.1.7); `restoreSession` shares one run under StrictMode. */
function RestoreOnLaunch() {
  useEffect(() => {
    if (api) void restoreSession(api);
  }, []);
  return null;
}

export default function RootLayout() {
  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <AppProviders>
        <SessionProvider>
          <RestoreOnLaunch />
          <RootNavigator />
        </SessionProvider>
      </AppProviders>
    </GestureHandlerRootView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  loading: {
    ...StyleSheet.absoluteFill,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#ffffff',
  },
});
