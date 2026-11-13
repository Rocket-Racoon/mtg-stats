/** Typed access to public (EXPO_PUBLIC_*) environment variables, inlined at bundle time. */
export const API_BASE_URL_VAR = 'EXPO_PUBLIC_API_BASE_URL';

export type EnvProblem = 'missing' | 'malformed';

export type EnvResult =
  | { ok: true; apiBaseUrl: string }
  | { ok: false; variable: typeof API_BASE_URL_VAR; problem: EnvProblem };

/** Validates the raw API base URL: it must be present and an absolute http(s) URL. */
export function parseApiBaseUrl(raw: string | undefined): EnvResult {
  const value = raw?.trim();
  if (!value) return { ok: false, variable: API_BASE_URL_VAR, problem: 'missing' };
  try {
    const url = new URL(value);
    if (url.protocol === 'http:' || url.protocol === 'https:') {
      return { ok: true, apiBaseUrl: value };
    }
  } catch {
    // falls through to malformed
  }
  return { ok: false, variable: API_BASE_URL_VAR, problem: 'malformed' };
}

// Must stay a direct `process.env.EXPO_PUBLIC_*` access: Babel only inlines that form.
export const env: EnvResult = parseApiBaseUrl(process.env.EXPO_PUBLIC_API_BASE_URL);
