import { act, screen } from 'expo-router/testing-library';
import { useSessionStore } from '../session/store';

/**
 * Signs in without the form (S-2.1.7 replaced the placeholder "Continue" button): waits for the
 * sign-in screen, which means the launch restore has finished, then signs in through the store.
 */
export async function signInViaStore() {
  await screen.findByLabelText('I have an invite');
  await act(async () => {
    await useSessionStore.getState().signIn({
      accessToken: 'test-access-token', // secrets-check: allow
      refreshToken: 'test-refresh-token', // secrets-check: allow
    });
  });
}
