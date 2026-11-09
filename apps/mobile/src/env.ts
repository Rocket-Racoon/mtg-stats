/** Typed access to public (EXPO_PUBLIC_*) environment variables, inlined at bundle time. */
export interface Env {
  apiBaseUrl: string;
}

export const DEFAULT_API_BASE_URL = 'http://localhost:8000/api/v1';

export const env: Env = {
  apiBaseUrl: process.env.EXPO_PUBLIC_API_BASE_URL ?? DEFAULT_API_BASE_URL,
};
