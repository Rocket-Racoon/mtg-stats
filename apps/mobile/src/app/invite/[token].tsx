import { ApiError, useInvitePreview, useRegister } from '@mtg-stats/api-client';
import { Stack, useLocalSearchParams } from 'expo-router';
import { useRef, useState } from 'react';
import { ScrollView, StyleSheet, Text } from 'react-native';
import {
  INVITE_INVALID_MESSAGE,
  OFFLINE_MESSAGE,
  SOMETHING_WENT_WRONG,
  fieldErrorsFromDetails,
  formErrorMessage,
  isInviteGone,
  isOffline,
} from '../../auth/errors';
import { AppButton } from '../../components/AppButton';
import { useColdStartBack } from '../../components/DeepLinkScreen';
import { FormError, TextField, type TextInputInstance } from '../../components/TextField';
import { useSession } from '../../session/SessionProvider';
import { useSessionStore } from '../../session/store';
import {
  validateDisplayName,
  validateEmail,
  validatePassword,
  validateUsername,
} from '../../session/validation';

const FIELDS = ['username', 'display_name', 'password', 'email'] as const;
type Field = (typeof FIELDS)[number];
type Errors = Partial<Record<Field, string>>;

/**
 * Target of mtgstats://invite/<token>, readable signed out: who invited you, then the
 * registration form. A successful registration signs the person in with the returned tokens.
 */
export default function Invite() {
  const { token = '' } = useLocalSearchParams<{ token: string }>();
  useColdStartBack();
  const preview = useInvitePreview(token);
  const register = useRegister();
  const { status, signIn } = useSession();
  const restoring = status === 'restoring';
  const [values, setValues] = useState<Record<Field, string>>({
    username: '',
    display_name: '',
    password: '',
    email: '',
  });
  const [errors, setErrors] = useState<Errors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [gone, setGone] = useState(false);
  const usernameRef = useRef<TextInputInstance>(null);
  const displayNameRef = useRef<TextInputInstance>(null);
  const passwordRef = useRef<TextInputInstance>(null);
  const emailRef = useRef<TextInputInstance>(null);

  const fieldRefs = {
    username: usernameRef,
    display_name: displayNameRef,
    password: passwordRef,
    email: emailRef,
  };
  const focusFirst = (found: Errors) => {
    const first = FIELDS.find((field) => found[field]);
    if (first) fieldRefs[first].current?.focus();
  };

  const submit = () => {
    // Registering while the startup restore runs would let a late refresh undo the new session.
    if (register.isPending || restoring) return;
    const found: Errors = {};
    const checks: Record<Field, string | null> = {
      username: validateUsername(values.username),
      display_name: validateDisplayName(values.display_name),
      password: validatePassword(values.password),
      email: validateEmail(values.email),
    };
    for (const field of FIELDS) {
      const message = checks[field];
      if (message) found[field] = message;
    }
    setErrors(found);
    setFormError(null);
    if (Object.keys(found).length > 0) {
      focusFirst(found);
      return;
    }
    register.mutate(
      {
        invite_token: token,
        username: values.username,
        display_name: values.display_name.trim(),
        password: values.password,
        email: values.email.trim(),
      },
      {
        onSuccess: async (tokens) => {
          if (!tokens.refresh_token) {
            setFormError(SOMETHING_WENT_WRONG); // not a mobile-mode response
            return;
          }
          // The root navigator lands on the pending link, or on Play when there is none.
          const { pendingHref, setPendingHref } = useSessionStore.getState();
          if (pendingHref === null) setPendingHref('/play');
          await signIn({ accessToken: tokens.access_token, refreshToken: tokens.refresh_token });
        },
        onError: (error) => {
          if (isInviteGone(error)) {
            setGone(true);
            return;
          }
          const failed: Errors = {};
          let message: string | null = formErrorMessage(error);
          if (error instanceof ApiError && !isOffline(error) && error.status !== 429) {
            if (error.status === 409 || error.code === 'username_taken') {
              failed.username = error.message;
              message = null;
            } else if (error.status === 422) {
              const mapped = fieldErrorsFromDetails(error);
              for (const field of FIELDS) {
                const text = mapped[field];
                if (text) failed[field] = text;
              }
              if (Object.keys(failed).length > 0) message = null;
            }
          }
          setErrors(failed);
          setFormError(message);
          focusFirst(failed);
        },
      },
    );
  };

  const header = (
    <>
      <Stack.Screen options={{ title: 'Invite' }} />
      <Text accessibilityRole="header" style={styles.title}>
        Invite
      </Text>
    </>
  );

  if (gone || (preview.isError && isInviteGone(preview.error))) {
    return (
      <ScrollView contentContainerStyle={styles.container} testID="invite-screen">
        {header}
        <FormError message={INVITE_INVALID_MESSAGE} testID="invite-gone" />
      </ScrollView>
    );
  }

  if (preview.isError) {
    return (
      <ScrollView contentContainerStyle={styles.container} testID="invite-screen">
        {header}
        <FormError
          message={isOffline(preview.error) ? OFFLINE_MESSAGE : SOMETHING_WENT_WRONG}
          testID="invite-preview-error"
        />
        <AppButton
          label={preview.isFetching ? 'Retrying…' : 'Retry'}
          disabled={preview.isFetching}
          busy={preview.isFetching}
          onPress={() => {
            void preview.refetch();
          }}
          testID="invite-retry"
        />
      </ScrollView>
    );
  }

  if (!preview.data) {
    return (
      <ScrollView contentContainerStyle={styles.container} testID="invite-screen">
        {header}
        <Text accessibilityLiveRegion="polite">Loading invite…</Text>
      </ScrollView>
    );
  }

  const set = (field: Field) => (value: string) => {
    setValues((current) => ({ ...current, [field]: value }));
  };

  return (
    <ScrollView
      contentContainerStyle={styles.container}
      keyboardShouldPersistTaps="handled"
      testID="invite-screen"
    >
      {header}
      <Text testID="invite-inviter">
        {preview.data.inviter_display_name} invited you to join their playgroup.
      </Text>
      {formError ? <FormError message={formError} testID="invite-form-error" /> : null}
      <TextField
        label="Username"
        testID="invite-username"
        inputRef={usernameRef}
        value={values.username}
        onChangeText={set('username')}
        error={errors.username}
        autoCapitalize="none"
        autoCorrect={false}
        autoComplete="username-new"
        returnKeyType="next"
        onSubmitEditing={() => displayNameRef.current?.focus()}
      />
      <TextField
        label="Display name"
        testID="invite-display-name"
        inputRef={displayNameRef}
        value={values.display_name}
        onChangeText={set('display_name')}
        error={errors.display_name}
        autoComplete="name"
        returnKeyType="next"
        onSubmitEditing={() => passwordRef.current?.focus()}
      />
      <TextField
        label="Password"
        testID="invite-password"
        inputRef={passwordRef}
        value={values.password}
        onChangeText={set('password')}
        error={errors.password}
        secureTextEntry
        autoCapitalize="none"
        autoComplete="new-password"
        returnKeyType="next"
        onSubmitEditing={() => emailRef.current?.focus()}
      />
      <TextField
        label="Email (optional)"
        testID="invite-email"
        inputRef={emailRef}
        value={values.email}
        onChangeText={set('email')}
        error={errors.email}
        keyboardType="email-address"
        autoCapitalize="none"
        autoCorrect={false}
        autoComplete="email"
        returnKeyType="go"
        onSubmitEditing={submit}
      />
      {restoring ? <Text accessibilityLiveRegion="polite">Restoring your session…</Text> : null}
      <AppButton
        label={register.isPending ? 'Creating account…' : 'Create account'}
        onPress={submit}
        disabled={register.isPending || restoring}
        busy={register.isPending}
        testID="invite-submit"
      />
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flexGrow: 1, alignItems: 'center', justifyContent: 'center', gap: 12, padding: 16 },
  title: { fontSize: 24, fontWeight: '600' },
});
