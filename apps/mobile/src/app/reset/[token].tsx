import { Stack, useLocalSearchParams } from 'expo-router';
import { DeepLinkScreen } from '../../components/DeepLinkScreen';

// Target of mtgstats://reset/<token>, readable signed out. S-2.1.9 builds the reset form here.
export default function Reset() {
  const { token } = useLocalSearchParams<{ token: string }>();
  return (
    <>
      <Stack.Screen options={{ title: 'Reset password' }} />
      <DeepLinkScreen
        title="Reset password"
        paramLabel="Token"
        value={token}
        testID="reset-screen"
      />
    </>
  );
}
