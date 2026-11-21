import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useApiClient } from '../client-context';
import type { components } from '../generated/schema';
import { cursorPageOptions } from '../pagination';
import { queryKey } from '../query-keys';

type PlaygroupCreate = components['schemas']['PlaygroupCreate'];
export type Playgroup = components['schemas']['PlaygroupOut'];
export type PlaygroupPage = components['schemas']['PlaygroupPageOut'];
export type PlaygroupRole = Playgroup['role'];

export const playgroupKeys = {
  /** `['playgroups', 'list', {}]`: the caller's playgroups. */
  list: () => queryKey('playgroups', 'list'),
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
