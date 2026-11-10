/** Typed access to build-time environment variables (see .env.example). */
export const env = {
  apiBaseUrl: import.meta.env.VITE_API_BASE_URL,
  appVersion: import.meta.env.VITE_APP_VERSION,
} as const;
