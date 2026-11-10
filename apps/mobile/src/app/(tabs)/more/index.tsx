import { StyleSheet, Text, View } from 'react-native';
import { AppButton } from '../../../components/AppButton';
import { useSession } from '../../../session/SessionProvider';

// Placeholder: settings and profile arrive with later stories.
export default function More() {
  const { signOut } = useSession();
  return (
    <View style={styles.container}>
      <Text accessibilityRole="header" style={styles.title}>
        More
      </Text>
      <AppButton label="Sign out" onPress={signOut} testID="sign-out" />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 12 },
  title: { fontSize: 24, fontWeight: '600' },
});
