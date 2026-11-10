# @mtg-stats/api-client

Typed `openapi-fetch` client for the MTG Stats API (`createApiClient`, `ApiError`), plus the
TanStack Query helpers shared by web and mobile. `@tanstack/react-query` and `react` are peer
dependencies.

## Writing hooks

Server data is fetched with **hand-written** hooks over the typed client (decision A4). There
is no hook generator, and none is to be added.

- **Where**: one module per domain, `src/hooks/<domain>.ts`, exported from `src/index.ts`.
  `src/hooks/system.ts` (`useHealthLive`, `useHealthReady`) is the template: copy it.
- **Client**: hooks call `useApiClient()` (the app mounts `<ApiClientProvider client={...}>`)
  and use `client.GET/POST/...`, which resolve to the response data and throw `ApiError`.
  Never call `fetch` directly. Pass TanStack's `signal` through for cancellation.
- **Keys**: build every key with `queryKey(domain, operation, params?)`, giving
  `[domain, operation, params]` (`params` is `{}` when there are none). `domain` is one of the
  architecture domains (`DOMAINS`). Keep a per-domain key object (see `systemKeys`) so
  mutations can reuse the keys.
- **Invalidation**: `queryClient.invalidateQueries({ queryKey: domainKey('decks') })`
  invalidates every query of the domain; use a full key for one operation.
- **Retries**: create the client with `createQueryClient()`. Queries do not retry on an
  `ApiError` of status 400-499; a 5xx or `network_error` is retried at most 2 times; anything
  else is not retried. Mutations never retry. Branch on `error.code`, never on `message`.
- **Infinite lists**: for a `Page<T>` endpoint (`{ items, next_cursor }`) use
  `cursorPageOptions`; a `null` `next_cursor` means no further pages:

```ts
export function useDecks(params: { limit?: number } = {}) {
  const client = useApiClient();
  return useInfiniteQuery(
    cursorPageOptions({
      queryKey: queryKey('decks', 'list', params),
      fetchPage: (cursor, signal) =>
        client.GET('/api/v1/decks', { params: { query: { ...params, cursor } }, signal }),
    }),
  );
}
```

(`/api/v1/decks` is hypothetical: the spec has no list endpoint yet.) Flatten the pages with
`data.pages.flatMap((page) => page.items)`.
