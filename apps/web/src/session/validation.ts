/** Client-side form checks (S-2.1.6). They mirror the API's bounds; the API stays authoritative. */

/** Length in code points, matching the API's Python `len()` (not UTF-16 units). */
function count(value: string): number {
  return [...value].length;
}

export const USERNAME_PATTERN = /^[A-Za-z0-9_.-]+$/;

export function validateUsername(value: string): string | null {
  if (count(value) < 3 || count(value) > 24) return 'Username must be 3 to 24 characters.';
  if (!USERNAME_PATTERN.test(value)) {
    return 'Username may only contain letters, numbers, dots, dashes and underscores.';
  }
  return null;
}

export function validatePassword(value: string): string | null {
  if (count(value) < 12 || count(value) > 128) return 'Password must be 12 to 128 characters.';
  return null;
}

export function validateDisplayName(value: string): string | null {
  const length = count(value.trim());
  if (length < 1 || length > 32) return 'Display name must be 1 to 32 characters.';
  return null;
}

export function validateEmail(value: string): string | null {
  if (count(value.trim()) > 320) return 'Email must be at most 320 characters.';
  return null;
}

export function requireValue(label: string, value: string): string | null {
  return value === '' ? `${label} is required.` : null;
}
