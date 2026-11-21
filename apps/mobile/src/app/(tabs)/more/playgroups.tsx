import { ApiError, useCreatePlaygroup, usePlaygroups } from '@mtg-stats/api-client';
import { Stack } from 'expo-router';
import { useRef, useState } from 'react';
import { AccessibilityInfo, ScrollView, StyleSheet, Text, View } from 'react-native';
import { fieldErrorsFromDetails, formErrorMessage } from '../../../auth/errors';
import { AppButton } from '../../../components/AppButton';
import { FormError, TextField, type TextInputInstance } from '../../../components/TextField';
import { validatePlaygroup, type PlaygroupErrors } from '../../../playgroups/validation';

/** Your playgroups and the create form (S-2.4.1). The playgroup home and switcher is S-2.4.5. */
export default function Playgroups() {
  const mutation = useCreatePlaygroup();
  const list = usePlaygroups();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [errors, setErrors] = useState<PlaygroupErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [status, setStatus] = useState('');
  const nameRef = useRef<TextInputInstance>(null);
  const descriptionRef = useRef<TextInputInstance>(null);

  const focusFirst = (found: PlaygroupErrors) => {
    if (found.name) nameRef.current?.focus();
    else if (found.description) descriptionRef.current?.focus();
  };

  const submit = () => {
    if (mutation.isPending) return;
    const found = validatePlaygroup(name, description);
    setErrors(found);
    setFormError(null);
    setStatus('');
    if (found.name ?? found.description) return focusFirst(found);
    mutation.mutate(
      { name: name.trim(), description },
      {
        onSuccess: (created) => {
          const message = `Created playgroup ${created.name}.`;
          setName('');
          setDescription('');
          setStatus(message);
          AccessibilityInfo.announceForAccessibility(message);
          nameRef.current?.focus();
        },
        onError: (error) => {
          if (error instanceof ApiError && error.code === 'validation_error') {
            const mapped = fieldErrorsFromDetails(error);
            const fieldErrors: PlaygroupErrors = {};
            if (mapped.name) fieldErrors.name = mapped.name;
            if (mapped.description) fieldErrors.description = mapped.description;
            if (fieldErrors.name ?? fieldErrors.description) {
              setErrors(fieldErrors);
              return focusFirst(fieldErrors);
            }
          }
          setFormError(formErrorMessage(error));
        },
      },
    );
  };

  const playgroups = list.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
      <Stack.Screen options={{ title: 'Playgroups' }} />
      <Text accessibilityRole="header" style={styles.title}>
        Create a playgroup
      </Text>
      {formError ? <FormError message={formError} testID="playgroup-form-error" /> : null}
      <TextField
        label="Name"
        testID="playgroup-name"
        inputRef={nameRef}
        value={name}
        onChangeText={setName}
        error={errors.name}
        autoComplete="off"
        returnKeyType="next"
        onSubmitEditing={() => descriptionRef.current?.focus()}
      />
      <TextField
        label="Description (optional)"
        testID="playgroup-description"
        inputRef={descriptionRef}
        value={description}
        onChangeText={setDescription}
        error={errors.description}
        multiline
        autoComplete="off"
      />
      <AppButton
        label={mutation.isPending ? 'Creating…' : 'Create playgroup'}
        onPress={submit}
        disabled={mutation.isPending}
        busy={mutation.isPending}
        testID="playgroup-submit"
      />
      {status ? (
        <Text role="status" accessibilityLiveRegion="polite" testID="playgroup-status">
          {status}
        </Text>
      ) : null}
      <Text accessibilityRole="header" style={styles.subtitle}>
        Your playgroups
      </Text>
      {list.isPending ? (
        <Text role="status" testID="playgroups-loading">
          Loading playgroups…
        </Text>
      ) : list.isError ? (
        <View style={styles.block}>
          <FormError message={formErrorMessage(list.error)} testID="playgroups-error" />
          <AppButton label="Retry" onPress={() => void list.refetch()} testID="playgroups-retry" />
        </View>
      ) : playgroups.length === 0 ? (
        <Text testID="playgroups-empty">You are not in any playgroup yet.</Text>
      ) : (
        <View style={styles.block} testID="playgroups-list">
          {playgroups.map((p) => (
            <Text key={p.id} style={styles.item}>
              {p.name} ({p.role})
            </Text>
          ))}
        </View>
      )}
      {list.hasNextPage ? (
        <AppButton
          label="Load more"
          onPress={() => void list.fetchNextPage()}
          disabled={list.isFetchingNextPage}
          busy={list.isFetchingNextPage}
          testID="playgroups-load-more"
        />
      ) : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flexGrow: 1, gap: 12, padding: 16 },
  title: { fontSize: 24, fontWeight: '600' },
  subtitle: { fontSize: 20, fontWeight: '600', marginTop: 12 },
  block: { gap: 8, alignSelf: 'stretch' },
  item: { fontSize: 16 },
});
