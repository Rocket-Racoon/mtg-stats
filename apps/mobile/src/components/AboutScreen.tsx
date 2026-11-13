import {
  FAN_CONTENT_DISCLAIMER,
  SCRYFALL_CREDIT,
  SCRYFALL_IMAGES_NOTE,
  type AttributionSegment,
} from '@mtg-stats/card-utils';
import Constants from 'expo-constants';
import { Stack } from 'expo-router';
import { ScrollView, StyleSheet, Text } from 'react-native';
import { ExternalLink } from './ExternalLink';

/** App version from the Expo config (`version` in app.json); "unknown" if the config has none. */
export function appVersion(): string {
  return Constants.expoConfig?.version ?? 'unknown';
}

/** Shared notice segments as one paragraph; links are inline, wording comes from card-utils. */
function Notice({ segments, testID }: { segments: readonly AttributionSegment[]; testID: string }) {
  return (
    <Text style={styles.body} testID={testID}>
      {segments.map((s) =>
        s.kind === 'text' ? (
          s.text
        ) : (
          <ExternalLink key={s.href} inline label={s.label} href={s.href} />
        ),
      )}
    </Text>
  );
}

/**
 * The About screen (S-1.7.5): version, Fan Content Policy disclaimer and Scryfall credit. One
 * component, mounted by both the More stack and the signed-out (auth) stack so the notices are
 * reachable signed in or out.
 */
export function AboutScreen() {
  return (
    <ScrollView contentContainerStyle={styles.container}>
      <Stack.Screen options={{ title: 'About' }} />
      <Text accessibilityRole="header" style={styles.title}>
        About
      </Text>
      <Text style={styles.body}>
        MTG Stats version <Text testID="app-version">{appVersion()}</Text>
      </Text>
      <Notice segments={FAN_CONTENT_DISCLAIMER} testID="fan-content-notice" />
      <Notice segments={SCRYFALL_CREDIT} testID="scryfall-credit" />
      <Text style={styles.body}>{SCRYFALL_IMAGES_NOTE}</Text>
      {/* S-1.7.4 adds the build profile and API URL here in dev builds. */}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 16, gap: 16 },
  title: { fontSize: 24, fontWeight: '600' },
  body: { fontSize: 16, lineHeight: 24 },
});
