/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

/** `version` from apps/web/package.json, a compile-time constant injected by vite.config.ts `define` (not an env setting). */
declare const __APP_VERSION__: string;
