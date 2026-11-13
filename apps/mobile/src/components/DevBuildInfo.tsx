import { ApiError, useHealthReady } from '@mtg-stats/api-client';
import { StyleSheet, Text, View } from 'react-native';
import { buildProfile } from '../buildInfo';
import { env } from '../env';
import { AppButton } from './AppButton';

type Checks = { database?: unknown; redis?: unknown };

function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'unexpected_response') {
      const checks = (error.details?.checks ?? {}) as Checks;
      return `API not ready (database ${String(checks.database ?? '?')}, redis ${String(checks.redis ?? '?')})`;
    }
    if (error.code === 'network_error') return 'API unreachable (network error)';
    return `API error (${error.code})`;
  }
  return 'API check failed';
}

/** Dev-build section of About (S-1.7.4): build profile, API URL and the live readiness result. */
export function DevBuildInfo() {
  const ready = useHealthReady();
  let status: string;
  if (ready.isPending) status = 'API: checking...';
  else if (ready.isError) status = describeError(ready.error);
  else {
    const { database, redis } = ready.data.checks;
    status = `API: reachable (database ${database}, redis ${redis})`;
  }
  return (
    <View style={styles.container} testID="dev-build-info">
      <Text accessibilityRole="header" style={styles.heading}>
        Developer info
      </Text>
      <Text style={styles.body}>
        Build profile: <Text testID="build-profile">{buildProfile()}</Text>
      </Text>
      <Text style={styles.body}>
        API base URL: <Text testID="about-api-base-url">{env.ok ? env.apiBaseUrl : 'not set'}</Text>
      </Text>
      <Text style={styles.body} testID="api-health-status" accessibilityLiveRegion="polite">
        {status}
      </Text>
      <AppButton
        label="Check API again"
        onPress={() => void ready.refetch()}
        testID="api-health-retry"
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { gap: 8 },
  heading: { fontSize: 18, fontWeight: '600' },
  body: { fontSize: 16, lineHeight: 24 },
});
