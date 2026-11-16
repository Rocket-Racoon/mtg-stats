import { Redirect, Stack, useLocalSearchParams } from 'expo-router';
import { useEffect } from 'react';
import { DeepLinkScreen } from '../../components/DeepLinkScreen';
import { useSession } from '../../session/SessionProvider';
import { useSessionStore } from '../../session/store';

// Target of mtgstats://join/<code> (used later by F-6.2). Signed in only: a signed-out visitor is
// sent to sign-in and the link is remembered (pendingHref) so signing in returns here.
export default function Join() {
  const { code } = useLocalSearchParams<{ code: string }>();
  const { status } = useSession();
  const signedOut = status === 'signed-out';

  useEffect(() => {
    if (signedOut) useSessionStore.getState().setPendingHref(`/join/${encodeURIComponent(code)}`);
  }, [signedOut, code]);

  if (status !== 'signed-in') return signedOut ? <Redirect href="/sign-in" /> : null;
  return (
    <>
      <Stack.Screen options={{ title: 'Join' }} />
      <DeepLinkScreen title="Join game" paramLabel="Code" value={code} testID="join-screen" />
    </>
  );
}
