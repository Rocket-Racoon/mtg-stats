import { Stack, router } from 'expo-router';
import { useState } from 'react';
import { ScrollView, StyleSheet, Text } from 'react-native';
import { AppButton } from '../../components/AppButton';
import { FormError, TextField } from '../../components/TextField';
import { parseInviteToken } from '../../session/parseInviteToken';

/** "I have an invite": paste the invite link or just its token, then continue to the invite. */
export default function HaveInvite() {
  const [value, setValue] = useState('');
  const [error, setError] = useState<string | null>(null);

  const submit = () => {
    const token = parseInviteToken(value);
    if (token === null) {
      setError('That is not an invite link or token. Paste the whole link or just the token.');
      return;
    }
    setError(null);
    router.push({ pathname: '/invite/[token]', params: { token } });
  };

  return (
    <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
      <Stack.Screen options={{ title: 'I have an invite' }} />
      <Text accessibilityRole="header" style={styles.title}>
        I have an invite
      </Text>
      <TextField
        label="Invite link or token"
        testID="have-invite-input"
        value={value}
        onChangeText={setValue}
        autoCapitalize="none"
        autoCorrect={false}
        returnKeyType="go"
        onSubmitEditing={submit}
      />
      {error ? <FormError message={error} testID="have-invite-error" /> : null}
      <AppButton label="Continue" onPress={submit} testID="have-invite-continue" />
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flexGrow: 1, alignItems: 'center', justifyContent: 'center', gap: 12, padding: 16 },
  title: { fontSize: 24, fontWeight: '600' },
});
