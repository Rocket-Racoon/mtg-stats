import { router, useFocusEffect } from 'expo-router';
import { useCallback } from 'react';
import { BackHandler, StyleSheet, Text, View } from 'react-native';
import { AppButton } from './AppButton';

function goBackOrHome() {
  if (router.canGoBack()) router.back();
  else router.replace('/');
}

/**
 * Placeholder for a deep-link target (`mtgstats://join/<code>`, `mtgstats://invite/<token>`).
 * It is pushed on top of the app; on a cold start there is no history, so back goes to the root.
 */
export function DeepLinkScreen({
  title,
  paramLabel,
  value,
  testID,
}: {
  title: string;
  paramLabel: string;
  value: string;
  testID: string;
}) {
  // Only while focused: a screen left underneath another must not swallow hardware back.
  useFocusEffect(
    useCallback(() => {
      const sub = BackHandler.addEventListener('hardwareBackPress', () => {
        if (router.canGoBack()) return false; // default: pop one screen
        router.replace('/');
        return true;
      });
      return () => sub.remove();
    }, []),
  );

  return (
    <View style={styles.container} testID={testID}>
      <Text accessibilityRole="header" style={styles.title}>
        {title}
      </Text>
      <Text>
        {paramLabel}: <Text testID={`${testID}-value`}>{value}</Text>
      </Text>
      <AppButton label="Back to app" onPress={goBackOrHome} />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 12 },
  title: { fontSize: 24, fontWeight: '600' },
});
