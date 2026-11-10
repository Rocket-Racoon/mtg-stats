import { Stack, useLocalSearchParams } from 'expo-router';
import { DeepLinkScreen } from '../../components/DeepLinkScreen';

// Target of mtgstats://join/<code> (used later by F-6.2).
export default function Join() {
  const { code } = useLocalSearchParams<{ code: string }>();
  return (
    <>
      <Stack.Screen options={{ title: 'Join' }} />
      <DeepLinkScreen title="Join game" paramLabel="Code" value={code} testID="join-screen" />
    </>
  );
}
