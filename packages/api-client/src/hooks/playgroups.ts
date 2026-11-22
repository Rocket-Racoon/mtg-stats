import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useApiClient } from '../client-context';
import type { components } from '../generated/schema';
import { cursorPageOptions } from '../pagination';
import { playgroupScopedKey, queryKey } from '../query-keys';

type PlaygroupCreate = components['schemas']['PlaygroupCreate'];
export type Playgroup = components['schemas']['PlaygroupOut'];
export type PlaygroupPage = components['schemas']['PlaygroupPageOut'];
export type PlaygroupRole = Playgroup['role'];
export type PlaygroupPlayer = components['schemas']['PlaygroupPlayerOut'];
export type PlaygroupPlayerPage = components['schemas']['PlaygroupPlayerPageOut'];
export type InviteJoinResult = components['schemas']['InviteJoinOut'];

export const playgroupKeys = {
  /** `['playgroups', 'list', {}]`: the caller's playgroups. */
  list: () => queryKey('playgroups', 'list'),
  /** `['playgroups', 'detail', {playgroupId}]`: one playgroup with the caller's role. */
  detail: (playgroupId: string) => playgroupScopedKey('playgroups', 'detail', playgroupId),
  /** `['playgroups', 'players', {playgroupId}]`: the roster (members, then guests). */
  players: (playgroupId: string) => playgroupScopedKey('playgroups', 'players', playgroupId),
};

/** `GET /api/v1/playgroups`: every playgroup the caller belongs to, with their role, by cursor. */
export function usePlaygroups() {
  const client = useApiClient();
  return useInfiniteQuery(
    cursorPageOptions({
      queryKey: playgroupKeys.list(),
      fetchPage: (cursor, signal): Promise<PlaygroupPage> =>
        client.GET('/api/v1/playgroups', {
          params: { query: cursor === null ? {} : { cursor } },
          signal,
        }),
    }),
  );
}

/**
 * `POST /api/v1/playgroups` (S-2.4.1): resolves to the new playgroup (201, caller is the owner)
 * and invalidates the list. A 422 `validation_error` carries field errors in `details`; the form
 * shows those inline and toasts other failures itself, so the global toast is off.
 */
export function useCreatePlaygroup() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    meta: { errorToast: false },
    mutationFn: (body: PlaygroupCreate): Promise<Playgroup> =>
      client.POST('/api/v1/playgroups', { body }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: playgroupKeys.list() }),
  });
}

/**
 * `GET /api/v1/playgroups/{id}`: one playgroup with the caller's role. A non-member or an unknown
 * id is a 404 `not_found` (no retry). The key carries the id, so a switch never shows old data.
 */
export function usePlaygroup(playgroupId: string, options: { enabled?: boolean } = {}) {
  const client = useApiClient();
  return useQuery({
    enabled: options.enabled ?? true,
    queryKey: playgroupKeys.detail(playgroupId),
    queryFn: ({ signal }): Promise<Playgroup> =>
      client.GET('/api/v1/playgroups/{playgroup_id}', {
        params: { path: { playgroup_id: playgroupId } },
        signal,
      }),
  });
}

/**
 * `GET /api/v1/playgroups/{id}/players` (S-2.2.4), by cursor: active members first (each group in
 * alphabetical order), then guests. Callers that need the whole roster follow `next_cursor`
 * until `hasNextPage` is false.
 */
export function usePlaygroupPlayers(playgroupId: string) {
  const client = useApiClient();
  return useInfiniteQuery(
    cursorPageOptions({
      queryKey: playgroupKeys.players(playgroupId),
      fetchPage: (cursor, signal): Promise<PlaygroupPlayerPage> =>
        client.GET('/api/v1/playgroups/{playgroup_id}/players', {
          params: {
            path: { playgroup_id: playgroupId },
            query: cursor === null ? {} : { cursor },
          },
          signal,
        }),
    }),
  );
}

/**
 * `POST /api/v1/invites/join`: the signed-in caller joins with an invite token (the token goes in
 * the body, never a URL). Resolves to `{already_member, playgroup}` and invalidates the list. A
 * revoked, expired, used-up or unknown token is a 410 `invite_no_longer_valid`. Errors are shown
 * inline, so the global toast is off.
 */
export function useJoinWithInvite() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    meta: { errorToast: false },
    mutationFn: (token: string): Promise<InviteJoinResult> =>
      client.POST('/api/v1/invites/join', { body: { token } }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: playgroupKeys.list() }),
  });
}
