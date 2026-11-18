import { StyleSheet, Text, View } from 'react-native';

// Placeholder: the Decks feature is not built yet.
export default function Decks() {
  return (
    <View style={styles.container} testID="decks-screen">
      <Text accessibilityRole="header" style={styles.title}>
        Decks
      </Text>
      <Text>Coming soon.</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 8 },
  title: { fontSize: 24, fontWeight: '600' },
});
