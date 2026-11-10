import { Link, router } from 'expo-router';
import { StyleSheet, Text, View } from 'react-native';
// Kept until S-1.5.3 hooks import `api`; proves the client bundles (S-1.5.2 AC4).
import '../../../api';
import { AppButton } from '../../../components/AppButton';
import { env } from '../../../env';
import { linkedPackages } from '../../../linkedPackages';

export default function Play() {
  return (
    <View style={styles.container}>
      <Text accessibilityRole="header" style={styles.title}>
        MTG Stats
      </Text>
      <Text testID="linked-packages">{linkedPackages.join(', ')}</Text>
      <Text testID="api-base-url">{env.apiBaseUrl}</Text>
      <AppButton label="Open details" onPress={() => router.push('/play/details')} />
      {__DEV__ ? <Link href="/dev/gesture-test">Gesture test</Link> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 8 },
  title: { fontSize: 24, fontWeight: '600' },
});
