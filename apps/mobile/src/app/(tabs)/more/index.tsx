import { router } from 'expo-router';
import { useState } from 'react';
import { Alert, StyleSheet, Text, View } from 'react-native';
import { formErrorMessage } from '../../../auth/errors';
import { AppButton } from '../../../components/AppButton';
import { FormError } from '../../../components/TextField';
import { useSignOut, useSignOutEverywhere } from '../../../session/useSignOut';

const SIGN_OUT_EVERYWHERE_TITLE = 'Sign out everywhere?';
const SIGN_OUT_EVERYWHERE_BODY =
  'This signs you out on every device, including this one. You will need to sign in again.';

const SIGN_OUT_EVERYWHERE_FAILED =
  'You are still signed in on this device, and your other devices may not have been signed out.';

// Placeholder: settings and profile arrive with later stories.
export default function More() {
  const signOut = useSignOut();
  const signOutEverywhere = useSignOutEverywhere();
  const [error, setError] = useState<string | null>(null);
  const busy = signOut.isPending || signOutEverywhere.isPending;

  const confirmEverywhere = () => {
    if (busy) return;
    Alert.alert(
      SIGN_OUT_EVERYWHERE_TITLE,
      SIGN_OUT_EVERYWHERE_BODY,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Sign out everywhere',
          style: 'destructive',
          onPress: () => {
            setError(null);
            signOutEverywhere.mutate(undefined, {
              onError: (e) => setError(`${formErrorMessage(e)} ${SIGN_OUT_EVERYWHERE_FAILED}`),
            });
          },
        },
      ],
      { cancelable: true },
    );
  };

  return (
    <View style={styles.container} testID="more-screen">
      <Text accessibilityRole="header" style={styles.title}>
        More
      </Text>
      {error ? (
        <>
          <FormError message={error} testID="sign-out-error" />
          <AppButton
            label="Sign out on this device only"
            onPress={() => {
              if (busy) return;
              setError(null);
              signOut.mutate();
            }}
            disabled={busy}
            busy={signOut.isPending}
            testID="sign-out-this-device"
          />
        </>
      ) : null}
      <AppButton label="About" onPress={() => router.push('/more/about')} testID="more-about" />
      <AppButton
        label="Change password"
        onPress={() => router.push('/more/change-password')}
        testID="more-change-password"
      />
      <AppButton
        label={signOut.isPending ? 'Signing out…' : 'Sign out'}
        onPress={() => {
          if (!busy) signOut.mutate();
        }}
        disabled={busy}
        busy={signOut.isPending}
        testID="sign-out"
      />
      <AppButton
        label="Sign out everywhere"
        onPress={confirmEverywhere}
        disabled={busy}
        busy={signOutEverywhere.isPending}
        testID="sign-out-everywhere"
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 12 },
  title: { fontSize: 24, fontWeight: '600' },
});
