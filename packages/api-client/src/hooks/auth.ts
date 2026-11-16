import { useMutation, useQuery } from '@tanstack/react-query';
import { useApiClient } from '../client-context';
import type { components } from '../generated/schema';
import { queryKey } from '../query-keys';

type LoginRequest = components['schemas']['LoginRequest'];
type RegisterRequest = components['schemas']['RegisterRequest'];
export type TokenResponse = components['schemas']['TokenResponse'];
export type InvitePreview = components['schemas']['InvitePreviewOut'];

export const authKeys = {
  invitePreview: (token: string) => queryKey('identity', 'invitePreview', { token }),
};

/**
 * `POST /api/v1/auth/login`. Resolves to the token response (web: the refresh token is in the
 * HttpOnly cookie). The caller stores the access token. Shows its error inline, so the app's
 * mutation toast is switched off with `meta.errorToast`.
 */
export function useSignIn() {
  const client = useApiClient();
  return useMutation({
    meta: { errorToast: false },
    mutationFn: (body: LoginRequest): Promise<TokenResponse> =>
      client.POST('/api/v1/auth/login', { body }),
  });
}

/** `POST /api/v1/auth/register`: registers with an invite and signs the person in (201). */
export function useRegister() {
  const client = useApiClient();
  return useMutation({
    meta: { errorToast: false },
    mutationFn: (body: RegisterRequest): Promise<TokenResponse> =>
      client.POST('/api/v1/auth/register', { body }),
  });
}

/**
 * `POST /api/v1/invites/preview`: who invited the visitor. A 410 (`invite_no_longer_valid`)
 * surfaces as an `ApiError`. Never retries automatically: the page offers a Retry button, and a
 * 410 is final.
 */
export function useInvitePreview(token: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: authKeys.invitePreview(token),
    enabled: token !== '',
    retry: false,
    queryFn: ({ signal }): Promise<InvitePreview> =>
      client.POST('/api/v1/invites/preview', { body: { token }, signal }),
  });
}
