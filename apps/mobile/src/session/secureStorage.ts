import * as SecureStore from 'expo-secure-store';

/** expo-secure-store keys (Android Keystore). Nothing else holds tokens: no AsyncStorage anywhere. */
export const ACCESS_TOKEN_KEY = 'mtgstats.accessToken'; // key name, not a secret // secrets-check: allow
export const REFRESH_TOKEN_KEY = 'mtgstats.refreshToken'; // key name, not a secret // secrets-check: allow

/**
 * Secure-store failures never propagate (S-2.1.7): a failed write leaves the new tokens in memory
 * so the session continues until the app closes, and a failed delete is ignored because memory is
 * already cleared. A read error counts as "no stored token". Tokens are never logged.
 */
export async function writeTokens(tokens: {
  accessToken?: string;
  refreshToken?: string;
}): Promise<void> {
  try {
    if (tokens.accessToken !== undefined) {
      await SecureStore.setItemAsync(ACCESS_TOKEN_KEY, tokens.accessToken);
    }
    if (tokens.refreshToken !== undefined) {
      await SecureStore.setItemAsync(REFRESH_TOKEN_KEY, tokens.refreshToken);
    }
  } catch {
    // Memory keeps the tokens; see the note above.
  }
}

export async function deleteTokens(): Promise<void> {
  for (const key of [ACCESS_TOKEN_KEY, REFRESH_TOKEN_KEY]) {
    try {
      await SecureStore.deleteItemAsync(key);
    } catch {
      // Memory is already cleared; see the note above.
    }
  }
}

export interface StoredTokens {
  accessToken: string | null;
  refreshToken: string | null;
}

export async function readTokens(): Promise<StoredTokens> {
  const read = async (key: string): Promise<string | null> => {
    try {
      const value = await SecureStore.getItemAsync(key);
      return typeof value === 'string' && value !== '' ? value : null;
    } catch {
      return null;
    }
  };
  return { accessToken: await read(ACCESS_TOKEN_KEY), refreshToken: await read(REFRESH_TOKEN_KEY) };
}
