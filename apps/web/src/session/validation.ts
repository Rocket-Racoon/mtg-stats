/** Client-side form checks (S-2.1.6). They mirror the API's bounds; the API stays authoritative. */

export const USERNAME_PATTERN = /^[A-Za-z0-9_.-]+$/;

export function validateUsername(value: string): string | null {
  if (value.length < 3 || value.length > 24) return 'Username must be 3 to 24 characters.';
  if (!USERNAME_PATTERN.test(value)) {
    return 'Username may only contain letters, numbers, dots, dashes and underscores.';
  }
  return null;
}

export function validatePassword(value: string): string | null {
  if (value.length < 12 || value.length > 128) return 'Password must be 12 to 128 characters.';
  return null;
}

export function validateDisplayName(value: string): string | null {
  const length = value.trim().length;
  if (length < 1 || length > 32) return 'Display name must be 1 to 32 characters.';
  return null;
}

export function validateEmail(value: string): string | null {
  if (value.trim().length > 320) return 'Email must be at most 320 characters.';
  return null;
}

export function requireValue(label: string, value: string): string | null {
  return value === '' ? `${label} is required.` : null;
}
