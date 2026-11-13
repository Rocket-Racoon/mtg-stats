import { Link, router } from 'expo-router';
import { StyleSheet, Text, View } from 'react-native';
import { AppButton } from '../../../components/AppButton';
import { linkedPackages } from '../../../linkedPackages';

export default function Play() {
  return (
    <View style={styles.container}>
      <Text accessibilityRole="header" style={styles.title}>
        MTG Stats
      </Text>
      <Text testID="linked-packages">{linkedPackages.join(', ')}</Text>
      <AppButton label="Open details" onPress={() => router.push('/play/details')} />
      {__DEV__ ? <Link href="/dev/gesture-test">Gesture test</Link> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 8 },
  title: { fontSize: 24, fontWeight: '600' },
});
