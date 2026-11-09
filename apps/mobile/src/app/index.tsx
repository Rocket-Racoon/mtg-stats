import { Link } from 'expo-router';
import { StyleSheet, Text, View } from 'react-native';
import { env } from '../env';
import { linkedPackages } from '../linkedPackages';

export default function Index() {
  return (
    <View style={styles.container}>
      <Text style={styles.title}>MTG Stats</Text>
      <Text testID="linked-packages">{linkedPackages.join(', ')}</Text>
      <Text testID="api-base-url">{env.apiBaseUrl}</Text>
      {__DEV__ ? <Link href="/dev/gesture-test">Gesture test</Link> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 8 },
  title: { fontSize: 24, fontWeight: '600' },
});
