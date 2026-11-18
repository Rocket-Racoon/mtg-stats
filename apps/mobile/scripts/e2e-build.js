// Builds the Android APK the Maestro e2e suite installs (S-1.8.6).
//
// Why a release build: it embeds the JS bundle, so the emulator needs no Metro server. `android/`
// is git-ignored and generated, so the build is a CNG prebuild followed by `assembleRelease`
// (Expo's template signs release with the debug keystore, which is fine for an emulator).
//
// The API base URL is baked into the bundle at build time through EXPO_PUBLIC_API_BASE_URL. From
// an emulator the host's Compose API is http://10.0.2.2:8000. MTG_E2E_BUILD=1 makes app.config.ts
// allow cleartext HTTP, which release builds block by default; no other build gets it.
//
// Usage: pnpm --filter @mtg-stats/mobile e2e:build   (Node stdlib only; Windows and Linux)
import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const mobileDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const androidDir = path.join(mobileDir, 'android');
const isWindows = process.platform === 'win32';
const DEFAULT_API_BASE_URL = 'http://10.0.2.2:8000';

const apiBaseUrl = process.env.EXPO_PUBLIC_API_BASE_URL?.trim() || DEFAULT_API_BASE_URL;
const env = {
  ...process.env,
  MTG_E2E_BUILD: '1',
  // Preview profile: app.config.ts skips the dev-only root .env, so only the value above is baked.
  APP_PROFILE: 'preview',
  EXPO_PUBLIC_API_BASE_URL: apiBaseUrl,
  // Non-interactive: no prompts (git status, dependency install) during prebuild.
  CI: '1',
  EXPO_NO_GIT_STATUS: '1',
};

// From an Android emulator, localhost is the emulator itself, not the host running the API.
const apiHost = (() => {
  try {
    return new URL(apiBaseUrl).hostname;
  } catch {
    return '';
  }
})();
if (['localhost', '127.0.0.1', '[::1]', '::1'].includes(apiHost)) {
  console.warn(
    `[e2e-build] WARNING: EXPO_PUBLIC_API_BASE_URL host "${apiHost}" is the emulator itself, not your machine; use ${DEFAULT_API_BASE_URL} (or adb reverse).`,
  );
}

/** Runs `command` with `args`; with no `args` it is one command line run through a shell. */
function run(label, command, args, options = {}) {
  console.log(`\n[e2e-build] ${label}: ${[command, ...(args ?? [])].join(' ')}`);
  // `shell: true` with an args array triggers Node's DEP0190, so shell runs pass one string.
  const result = args
    ? spawnSync(command, args, { stdio: 'inherit', env, ...options })
    : spawnSync(command, { stdio: 'inherit', env, shell: true, ...options });
  if (result.error || result.status !== 0) {
    console.error(
      `[e2e-build] ${label} failed: ${result.error?.message ?? `exit ${result.status}`}`,
    );
    process.exit(result.status || 1);
  }
}

// Run Expo's own entry point with node, so no .cmd shim or shell is involved.
const expoCli = createRequire(import.meta.url).resolve('expo/bin/cli');
run('prebuild', process.execPath, [
  expoCli,
  'prebuild',
  '--platform',
  'android',
  '--clean',
  '--no-install',
]);

// gradlew.bat needs a shell on Windows; elsewhere `sh` avoids depending on the executable bit.
if (isWindows) {
  run('gradle', 'gradlew.bat assembleRelease', undefined, { cwd: androidDir });
} else {
  run('gradle', 'sh', ['gradlew', 'assembleRelease'], { cwd: androidDir });
}

const apk = path.join(androidDir, 'app', 'build', 'outputs', 'apk', 'release', 'app-release.apk');
console.log(`\n[e2e-build] APK: ${apk}`);
console.log(`[e2e-build] API base URL baked in: ${apiBaseUrl}`);
