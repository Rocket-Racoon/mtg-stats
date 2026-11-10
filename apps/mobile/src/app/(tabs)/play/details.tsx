import { Stack } from 'expo-router';
import { StyleSheet, Text, View } from 'react-native';

// Placeholder detail screen: gives the Play tab a real stack to go back within.
export default function PlayDetails() {
  return (
    <View style={styles.container}>
      <Stack.Screen options={{ title: 'Play details' }} />
      <Text accessibilityRole="header" style={styles.title}>
        Play details
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  title: { fontSize: 20, fontWeight: '600' },
});
