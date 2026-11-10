/**
 * Strips the `/api/v1` prefix (and a trailing slash) from a configured API base URL, leaving the
 * origin. The generated operation paths already start with `/api/v1`. Plain string handling on
 * purpose: React Native's `URL` does not implement `origin`.
 */
export function apiOrigin(apiBaseUrl: string): string {
  return apiBaseUrl
    .trim()
    .replace(/\/+$/, '')
    .replace(/\/api\/v1$/, '');
}
