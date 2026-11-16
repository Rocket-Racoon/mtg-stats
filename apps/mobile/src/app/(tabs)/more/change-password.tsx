import { ApiError, useChangePassword } from '@mtg-stats/api-client';
import { Stack } from 'expo-router';
import { useRef, useState } from 'react';
import { AccessibilityInfo, ScrollView, StyleSheet, Text } from 'react-native';
import {
  SOMETHING_WENT_WRONG,
  fieldErrorsFromDetails,
  formErrorMessage,
  isOffline,
} from '../../../auth/errors';
import { AppButton } from '../../../components/AppButton';
import { FormError, TextField, type TextInputInstance } from '../../../components/TextField';
import { useSession } from '../../../session/SessionProvider';
import { requireValue, validatePassword } from '../../../session/validation';

interface Errors {
  current?: string;
  next?: string;
  confirm?: string;
}

const PASSWORD_CHANGED = 'Password changed.';

/** Change my password (S-2.1.8). On success the new tokens replace the stored session. */
export default function ChangePassword() {
  const { signIn } = useSession();
  const mutation = useChangePassword();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [errors, setErrors] = useState<Errors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [status, setStatus] = useState('');
  const currentRef = useRef<TextInputInstance>(null);
  const nextRef = useRef<TextInputInstance>(null);
  const confirmRef = useRef<TextInputInstance>(null);

  const submit = () => {
    if (mutation.isPending) return;
    const currentError = requireValue('Current password', current);
    const nextError = requireValue('New password', next) ?? validatePassword(next);
    const confirmError =
      requireValue('Confirm new password', confirm) ??
      (confirm !== next ? "Passwords don't match." : null);
    const found: Errors = {};
    if (currentError) found.current = currentError;
    if (nextError) found.next = nextError;
    if (confirmError) found.confirm = confirmError;
    setErrors(found);
    setFormError(null);
    setStatus('');
    if (found.current) return currentRef.current?.focus();
    if (found.next) return nextRef.current?.focus();
    if (found.confirm) return confirmRef.current?.focus();
    mutation.mutate(
      { current_password: current, new_password: next },
      {
        onSuccess: (tokens) => {
          if (!tokens.refresh_token) {
            setFormError(SOMETHING_WENT_WRONG); // not a mobile-mode response
            return;
          }
          void signIn({ accessToken: tokens.access_token, refreshToken: tokens.refresh_token });
          setCurrent('');
          setNext('');
          setConfirm('');
          setStatus(PASSWORD_CHANGED);
          AccessibilityInfo.announceForAccessibility(PASSWORD_CHANGED);
        },
        onError: (error) => {
          if (error instanceof ApiError && error.code === 'invalid_current_password') {
            const mapped = fieldErrorsFromDetails(error);
            setErrors({ current: mapped.current_password ?? error.message });
            setCurrent('');
            currentRef.current?.focus();
          } else if (error instanceof ApiError && error.status === 422) {
            const mapped = fieldErrorsFromDetails(error);
            const fieldErrors: Errors = {};
            if (mapped.current_password) fieldErrors.current = mapped.current_password;
            if (mapped.new_password) fieldErrors.next = mapped.new_password;
            if (Object.keys(fieldErrors).length > 0) {
              setErrors(fieldErrors);
              if (fieldErrors.current) currentRef.current?.focus();
              else nextRef.current?.focus();
            } else setFormError(SOMETHING_WENT_WRONG);
          } else if (isOffline(error) || (error instanceof ApiError && error.status === 429)) {
            setFormError(formErrorMessage(error));
          } else {
            setFormError(SOMETHING_WENT_WRONG);
          }
        },
      },
    );
  };

  return (
    <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
      <Stack.Screen options={{ title: 'Change password' }} />
      <Text accessibilityRole="header" style={styles.title}>
        Change password
      </Text>
      {formError ? <FormError message={formError} testID="change-password-form-error" /> : null}
      <TextField
        label="Current password"
        testID="change-password-current"
        inputRef={currentRef}
        value={current}
        onChangeText={setCurrent}
        error={errors.current}
        secureTextEntry
        autoCapitalize="none"
        autoComplete="current-password"
        textContentType="password"
        returnKeyType="next"
        onSubmitEditing={() => nextRef.current?.focus()}
      />
      <TextField
        label="New password"
        testID="change-password-new"
        inputRef={nextRef}
        value={next}
        onChangeText={setNext}
        error={errors.next}
        secureTextEntry
        autoCapitalize="none"
        autoComplete="new-password"
        textContentType="newPassword"
        returnKeyType="next"
        onSubmitEditing={() => confirmRef.current?.focus()}
      />
      <TextField
        label="Confirm new password"
        testID="change-password-confirm"
        inputRef={confirmRef}
        value={confirm}
        onChangeText={setConfirm}
        error={errors.confirm}
        secureTextEntry
        autoCapitalize="none"
        autoComplete="new-password"
        textContentType="newPassword"
        returnKeyType="go"
        onSubmitEditing={submit}
      />
      <AppButton
        label={mutation.isPending ? 'Changing password…' : 'Change password'}
        onPress={submit}
        disabled={mutation.isPending}
        busy={mutation.isPending}
        testID="change-password-submit"
      />
      {status ? (
        <Text role="status" accessibilityLiveRegion="polite" testID="change-password-status">
          {status}
        </Text>
      ) : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flexGrow: 1, alignItems: 'center', justifyContent: 'center', gap: 12, padding: 16 },
  title: { fontSize: 24, fontWeight: '600' },
});
