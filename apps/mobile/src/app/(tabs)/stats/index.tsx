import { StyleSheet, Text, View } from 'react-native';

// Placeholder: the Stats feature is not built yet.
export default function Stats() {
  return (
    <View style={styles.container} testID="stats-screen">
      <Text accessibilityRole="header" style={styles.title}>
        Stats
      </Text>
      <Text>Coming soon.</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 8 },
  title: { fontSize: 24, fontWeight: '600' },
});
