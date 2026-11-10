/** The API domains (architecture §4.1). The first element of every query key. */
export const DOMAINS = [
  'system',
  'identity',
  'playgroups',
  'cards',
  'decks',
  'brackets',
  'games',
  'stats',
  'collection',
  'leagues',
] as const;

export type Domain = (typeof DOMAINS)[number];

/** Query parameters of one operation. Always an object; `{}` when there are none. */
export type QueryParams = Readonly<Record<string, unknown>>;

/** Every query key has the shape `[domain, operation, params]`. */
export type QueryKey<D extends Domain = Domain> = readonly [
  domain: D,
  operation: string,
  params: QueryParams,
];

/**
 * Builds `[domain, operation, params]`. `params` defaults to `{}` (never omitted), so keys
 * always have three elements. TanStack hashes objects by value with sorted keys, so param
 * order does not matter.
 */
export function queryKey<D extends Domain>(
  domain: D,
  operation: string,
  params: QueryParams = {},
): QueryKey<D> {
  return [domain, operation, params];
}

/** `[domain]`: a prefix of every key in that domain. Pass it to `invalidateQueries`. */
export function domainKey<D extends Domain>(domain: D): readonly [D] {
  return [domain];
}
