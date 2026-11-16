# @mtg-stats/api-client

Typed `openapi-fetch` client for the MTG Stats API (`createApiClient`, `ApiError`), plus the
TanStack Query helpers shared by web and mobile. `@tanstack/react-query` and `react` are peer
dependencies.

## Writing hooks

Server data is fetched with **hand-written** hooks over the typed client (decision A4). There
is no hook generator, and none is to be added.

- **One TanStack copy**: apps import TanStack Query only through `@mtg-stats/api-client`
  (`QueryClientProvider`, `useQueryClient`, `useQuery`, `useInfiniteQuery`, `useMutation`,
  `createQueryClient`), never from `@tanstack/react-query` directly. A second copy would give
  the app's provider and the package's hooks different React contexts.
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

## Auth: token store, Bearer header and refresh

Pass a `tokenStore` (and optionally `onSessionExpired`) to `createApiClient`. The package never needs
app-specific code; the two platforms differ only in the store they supply.

```ts
interface TokenStore {
  getAccessToken(): string | null | Promise<string | null>;
  getRefreshToken?(): string | null | Promise<string | null>; // omit for cookie mode
  setTokens(tokens: { accessToken: string; refreshToken?: string }): void | Promise<void>;
  clear(): void | Promise<void>;
}
```

- **Header**: while an access token exists, every request carries `Authorization: Bearer <token>`.
- **Refresh**: a 401 (except from `/auth/login`, `/auth/refresh` and `/auth/logout`) triggers one refresh,
  then the original request is retried once. A 401 on the retry rejects and does not refresh again.
- **Single-flight**: at most one refresh runs per client; concurrent 401s await the same call. A request
  whose token is already stale (another request refreshed) retries with the current token without refreshing.
- **Cookie mode (web)**: the store has no refresh token. The refresh is `POST /api/v1/auth/refresh` with
  `credentials: "include"` and no body, run inside `navigator.locks` (`mtgstats-auth-refresh`) when
  available so tabs do not race. The access token is kept in memory; the refresh token is the HttpOnly cookie.
- **Body mode (mobile)**: `getRefreshToken()` returns a token. The refresh sends `X-Client: mobile` and
  `{"refresh_token": ...}`, and `setTokens` receives the rotated refresh token.
- **Rejected refresh** (4xx other than 408/429, or an invalid body): the store is cleared, then
  `onSessionExpired` runs once, and waiting requests reject with the original 401 `ApiError`.
  **Transient failures** (network, 5xx, 408, 429) leave the store alone and reject with that failure's `ApiError`.
- Requests to `/api/v1/auth/*` are sent with `credentials: "include"` so the cookie is stored and sent.
- **Middleware order**: the auth hook runs its `onResponse` last (openapi-fetch runs `onResponse` in reverse
  order). A user `onResponse` sees the original 401, and the retried response passes through no middleware.
- **Caller-supplied `Authorization`**: when the caller sets its own `Authorization` header, a 401 never
  refreshes, retries, clears the store or calls `onSessionExpired`; it rejects as usual.
- **Caveats**: an explicit `credentials: "same-origin"` on an auth path cannot be told from the default, so
  it is upgraded to `include`. A 401 arriving after a rejected refresh has settled starts another refresh,
  which may call `onSessionExpired` again, so make the callback idempotent. If `getRefreshToken` or
  `setTokens` throws, that raw error rejects the waiting requests and the store is not cleared.
  If the caller aborts while a refresh runs, it gets an `AbortError`; the refresh side effects still happen once.
