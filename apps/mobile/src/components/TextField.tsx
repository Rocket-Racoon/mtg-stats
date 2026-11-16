import type { ComponentRef, Ref } from 'react';
import { StyleSheet, Text, TextInput, View, type TextInputProps } from 'react-native';
import { MIN_TOUCH_TARGET } from './AppButton';

/** The instance behind a `TextInput` ref (`focus()` and friends). */
export type TextInputInstance = ComponentRef<typeof TextInput>;

type Props = Omit<TextInputProps, 'style'> & {
  label: string;
  /** Shown under the field and announced politely. */
  error?: string | null;
  inputRef?: Ref<TextInputInstance>;
};

/** A labelled text input: visible label, accessibility label, and an announced error below it. */
export function TextField({ label, error, inputRef, testID, ...input }: Props) {
  return (
    <View style={styles.field}>
      <Text style={styles.label}>{label}</Text>
      <TextInput
        ref={inputRef}
        accessibilityLabel={label}
        testID={testID}
        style={[styles.input, error ? styles.inputError : null]}
        {...input}
      />
      {error ? (
        <Text
          accessibilityRole="alert"
          accessibilityLiveRegion="polite"
          style={styles.error}
          testID={testID ? `${testID}-error` : undefined}
        >
          {error}
        </Text>
      ) : null}
    </View>
  );
}

/** A form-level error, announced when it appears. */
export function FormError({ message, testID }: { message: string; testID?: string }) {
  return (
    <Text
      accessibilityRole="alert"
      accessibilityLiveRegion="polite"
      style={styles.error}
      testID={testID}
    >
      {message}
    </Text>
  );
}

const styles = StyleSheet.create({
  field: { alignSelf: 'stretch', gap: 4 },
  label: { fontSize: 14, fontWeight: '600' },
  input: {
    minHeight: MIN_TOUCH_TARGET,
    borderWidth: 1,
    borderColor: '#9ca3af',
    borderRadius: 8,
    paddingHorizontal: 12,
    fontSize: 16,
  },
  inputError: { borderColor: '#b91c1c' },
  error: { color: '#b91c1c', fontSize: 14 },
});
