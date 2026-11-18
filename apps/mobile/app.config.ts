import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { parseEnv } from 'node:util';
import type { ConfigContext, ExpoConfig } from 'expo/config';
import { AndroidConfig, withAndroidManifest, type ConfigPlugin } from 'expo/config-plugins';

/** Build profiles the app knows about; anything else is treated as `development`. */
export const BUILD_PROFILES = ['development', 'preview', 'production'] as const;
export type BuildProfile = (typeof BUILD_PROFILES)[number];

/**
 * The build profile: `EAS_BUILD_PROFILE` (set by EAS Build, E-11), else `APP_PROFILE` (set by
 * hand for local builds), else `development`. An unrecognised value falls back to `development`.
 */
export function resolveBuildProfile(env: Record<string, string | undefined>): BuildProfile {
  const raw = (env.EAS_BUILD_PROFILE?.trim() || env.APP_PROFILE?.trim() || '').toLowerCase();
  return (BUILD_PROFILES as readonly string[]).includes(raw)
    ? (raw as BuildProfile)
    : 'development';
}

const API_BASE_URL_VAR = 'EXPO_PUBLIC_API_BASE_URL';

/**
 * Expo only reads `apps/mobile/.env`. Mobile shares the single root `.env` with the API, so when
 * the variable is not already set (shell or `apps/mobile/.env` win) it is copied from the root
 * file into `process.env` here. Expo evaluates this config before Metro starts, and the Babel
 * inlining of `EXPO_PUBLIC_*` reads `process.env`, so the value reaches the bundle. Development
 * profile only.
 */
export function loadRootEnv(env: Record<string, string | undefined>, rootDir: string): void {
  // Only the local development profile reads the dev machine's root .env. A preview or production
  // build must get the value from its own environment (eas.json env, E-11), so a missing value
  // shows the config error screen instead of baking a LAN address into the binary.
  if (resolveBuildProfile(env) !== 'development') return;
  if (env[API_BASE_URL_VAR]?.trim()) return;
  const file = path.join(rootDir, '.env');
  if (!existsSync(file)) return;
  const value = parseEnv(readFileSync(file, 'utf8'))[API_BASE_URL_VAR];
  if (value?.trim()) env[API_BASE_URL_VAR] = value;
}

/** True only for the Maestro e2e build (S-1.8.6): `MTG_E2E_BUILD` must be exactly `1`. */
export function isE2eBuild(env: Record<string, string | undefined>): boolean {
  return env.MTG_E2E_BUILD === '1';
}

/**
 * Allows cleartext HTTP on the main `<application>`. Android blocks it in release builds, and the
 * e2e APK talks to the host's Compose API over plain HTTP (`http://10.0.2.2:8000`).
 */
export const withE2eCleartext: ConfigPlugin = (config) =>
  withAndroidManifest(config, (mod) => {
    const app = AndroidConfig.Manifest.getMainApplicationOrThrow(mod.modResults);
    app.$['android:usesCleartextTraffic'] = 'true';
    return mod;
  });

export default ({ config }: ConfigContext): ExpoConfig => {
  loadRootEnv(process.env, path.resolve(__dirname, '../..'));
  const resolved: ExpoConfig = {
    ...config,
    name: config.name ?? 'MTG Stats',
    slug: config.slug ?? 'mtg-stats',
    extra: { ...config.extra, buildProfile: resolveBuildProfile(process.env) },
  };
  // Cleartext HTTP is applied for the e2e build only. A production or preview build must never
  // permit it, so without MTG_E2E_BUILD=1 the config (and its plugins) is exactly what app.json
  // describes. The plugin is applied directly because ExpoConfig's `plugins` type has no room
  // for function plugins; the effect on prebuild is the same as listing it.
  return isE2eBuild(process.env) ? withE2eCleartext(resolved) : resolved;
};
