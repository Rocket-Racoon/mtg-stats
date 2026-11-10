import { Stack, useLocalSearchParams } from 'expo-router';
import { DeepLinkScreen } from '../../components/DeepLinkScreen';

// Target of mtgstats://invite/<token> (used later by F-2.1).
export default function Invite() {
  const { token } = useLocalSearchParams<{ token: string }>();
  return (
    <>
      <Stack.Screen options={{ title: 'Invite' }} />
      <DeepLinkScreen title="Invite" paramLabel="Token" value={token} testID="invite-screen" />
    </>
  );
}
