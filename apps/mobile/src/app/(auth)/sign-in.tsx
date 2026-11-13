import { Stack, router } from 'expo-router';
import { StyleSheet, Text, View } from 'react-native';
import { AppButton } from '../../components/AppButton';
import { useSession } from '../../session/SessionProvider';

// Placeholder: the real sign-in screens come from S-2.1.7.
export default function SignIn() {
  const { signIn } = useSession();
  return (
    <View style={styles.container}>
      <Stack.Screen options={{ title: 'Sign in' }} />
      <Text accessibilityRole="header" style={styles.title}>
        Sign in
      </Text>
      <Text>Placeholder: sign-in arrives with S-2.1.7.</Text>
      <AppButton label="Continue (placeholder)" onPress={signIn} testID="sign-in-continue" />
      <AppButton label="About" onPress={() => router.push('/about')} testID="sign-in-about" />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 12 },
  title: { fontSize: 24, fontWeight: '600' },
});
