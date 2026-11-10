/** Typed access to build-time environment variables (see .env.example). */
export const env = {
  apiBaseUrl: import.meta.env.VITE_API_BASE_URL,
  appVersion: __APP_VERSION__,
} as const;
