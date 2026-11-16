import { useSignIn } from '@mtg-stats/api-client';
import { Stack, router } from 'expo-router';
import { useRef, useState } from 'react';
import { ScrollView, StyleSheet, Text } from 'react-native';
import { SOMETHING_WENT_WRONG, formErrorMessage } from '../../auth/errors';
import { AppButton } from '../../components/AppButton';
import { FormError, TextField, type TextInputInstance } from '../../components/TextField';
import { useSession } from '../../session/SessionProvider';
import { requireValue } from '../../session/validation';

/**
 * Sign in with username and password (decision C1). On success the session store signs in; the
 * root navigator then moves to the pending deep link or the Play tab.
 */
export default function SignIn() {
  const { status, signIn } = useSession();
  const mutation = useSignIn();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [errors, setErrors] = useState<{ username?: string; password?: string }>({});
  const [formError, setFormError] = useState<string | null>(null);
  const usernameRef = useRef<TextInputInstance>(null);
  const passwordRef = useRef<TextInputInstance>(null);
  const blocked = status === 'restoring' || mutation.isPending;

  const submit = () => {
    if (blocked) return;
    const found = {
      username: requireValue('Username', username) ?? undefined,
      password: requireValue('Password', password) ?? undefined,
    };
    setErrors(found);
    setFormError(null);
    if (found.username) return usernameRef.current?.focus();
    if (found.password) return passwordRef.current?.focus();
    mutation.mutate(
      { username, password },
      {
        onSuccess: (tokens) => {
          if (!tokens.refresh_token) {
            setFormError(SOMETHING_WENT_WRONG); // not a mobile-mode response
            return;
          }
          void signIn({ accessToken: tokens.access_token, refreshToken: tokens.refresh_token });
        },
        onError: (error) => {
          setFormError(formErrorMessage(error));
          setPassword('');
          passwordRef.current?.focus();
        },
      },
    );
  };

  return (
    <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
      <Stack.Screen options={{ title: 'Sign in' }} />
      <Text accessibilityRole="header" style={styles.title}>
        Sign in
      </Text>
      {formError ? <FormError message={formError} testID="sign-in-form-error" /> : null}
      <TextField
        label="Username"
        testID="sign-in-username"
        inputRef={usernameRef}
        value={username}
        onChangeText={setUsername}
        error={errors.username}
        autoCapitalize="none"
        autoCorrect={false}
        autoComplete="username"
        textContentType="username"
        returnKeyType="next"
        onSubmitEditing={() => passwordRef.current?.focus()}
      />
      <TextField
        label="Password"
        testID="sign-in-password"
        inputRef={passwordRef}
        value={password}
        onChangeText={setPassword}
        error={errors.password}
        secureTextEntry
        autoCapitalize="none"
        autoComplete="current-password"
        textContentType="password"
        returnKeyType="go"
        onSubmitEditing={submit}
      />
      <AppButton
        label={mutation.isPending ? 'Signing in�' : 'Sign in'}
        onPress={submit}
        disabled={blocked}
        busy={mutation.isPending}
        testID="sign-in-submit"
      />
      <AppButton
        label="I have an invite"
        onPress={() => router.push('/have-invite')}
        testID="sign-in-have-invite"
      />
      <AppButton label="About" onPress={() => router.push('/about')} testID="sign-in-about" />
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flexGrow: 1, alignItems: 'center', justifyContent: 'center', gap: 12, padding: 16 },
  title: { fontSize: 24, fontWeight: '600' },
});
