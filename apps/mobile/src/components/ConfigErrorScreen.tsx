import { ScrollView, StyleSheet, Text } from 'react-native';
import { API_BASE_URL_VAR, type EnvProblem } from '../env';
import { useHideSplashOnMount } from '../session/splash';

/**
 * Shown instead of the app when a required environment variable is missing or malformed, so the
 * failure is visible rather than silent. Deliberately free of providers, navigation and the API.
 */
export function ConfigErrorScreen({ problem = 'missing' }: { problem?: EnvProblem }) {
  useHideSplashOnMount();
  return (
    <ScrollView contentContainerStyle={styles.container} testID="config-error-screen">
      <Text accessibilityRole="header" style={styles.title}>
        Configuration error
      </Text>
      <Text style={styles.body}>
        {problem === 'missing'
          ? 'The API address is not set. Set the variable '
          : 'The API address is not a valid http(s) URL. Fix the variable '}
        <Text style={styles.code} testID="config-error-variable">
          {API_BASE_URL_VAR}
        </Text>
        .
      </Text>
      <Text style={styles.body}>
        Example: {API_BASE_URL_VAR}=http://192.168.1.20:8000/api/v1 in the root .env file, then stop
        Expo and run pnpm dev:mobile --clear.
      </Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flexGrow: 1, justifyContent: 'center', padding: 24, gap: 16 },
  title: { fontSize: 24, fontWeight: '600' },
  body: { fontSize: 16, lineHeight: 24 },
  code: { fontFamily: 'monospace', fontWeight: '600' },
});
