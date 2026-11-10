// @vitest-environment node
// S-1.6.6 AC2: the version on /about is injected from apps/web/package.json by vite.config.ts.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { loadConfigFromFile } from 'vite';
import { describe, expect, it } from 'vitest';
import { env } from '../src/env';

const webRoot = fileURLToPath(new URL('..', import.meta.url));
const pkg = JSON.parse(readFileSync(webRoot + 'package.json', 'utf8')) as { version: string };

describe('app version', () => {
  it('env.appVersion is the package.json version, as a semver string', () => {
    expect(env.appVersion).toBe(pkg.version);
    expect(env.appVersion).toMatch(/^\d+\.\d+\.\d+/);
  });

  it('vite define injects the package.json version (not a hard-coded value)', async () => {
    const loaded = await loadConfigFromFile(
      { command: 'serve', mode: 'development' },
      webRoot + 'vite.config.ts',
    );
    expect(loaded?.config.define?.__APP_VERSION__).toBe(JSON.stringify(pkg.version));
  });
});
