import { resolve as resolvePath } from 'node:path';
import { createServer, loadConfigFromFile, type UserConfig } from 'vite';
import { afterEach, describe, expect, it, vi } from 'vitest';

// Vitest runs with the web workspace as cwd (jsdom's import.meta.url is not a file: URL).
const webRoot = process.cwd();
const configFile = resolvePath(webRoot, 'vite.config.ts');

// The config module relies on a real file:// import.meta.url, so it is loaded by Vite's own
// loader (as `vite build` does) instead of being imported into the jsdom test environment.
const resolve = async (command: 'build' | 'serve', mode = 'production'): Promise<UserConfig> => {
  const loaded = await loadConfigFromFile({ command, mode }, configFile);
  if (!loaded) throw new Error('vite.config.ts not found');
  return loaded.config;
};

afterEach(() => {
  vi.unstubAllEnvs();
});

describe('vite config: VITE_API_BASE_URL requirement (S-1.6.1 AC2)', () => {
  it('throws on build when VITE_API_BASE_URL is unset, naming the variable', async () => {
    vi.stubEnv('VITE_API_BASE_URL', '');
    await expect(resolve('build')).rejects.toThrow(/VITE_API_BASE_URL/);
  });

  it('throws on build when VITE_API_BASE_URL is only whitespace', async () => {
    vi.stubEnv('VITE_API_BASE_URL', '   ');
    await expect(resolve('build')).rejects.toThrow(/VITE_API_BASE_URL/);
  });

  it('does not throw on build when VITE_API_BASE_URL is set', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'https://api.example.test/api/v1');
    await expect(resolve('build')).resolves.toBeDefined();
  });

  it('does not throw on serve when VITE_API_BASE_URL is unset', async () => {
    vi.stubEnv('VITE_API_BASE_URL', '');
    await expect(resolve('serve', 'development')).resolves.toBeDefined();
  });
});

describe('vite config: dev server (S-1.6.1 AC1)', () => {
  it('pins the dev server to port 5173 with strictPort', async () => {
    vi.stubEnv('VITE_API_BASE_URL', '');
    const cfg = await resolve('serve', 'development');
    expect(cfg.server?.port).toBe(5173);
    expect(cfg.server?.strictPort).toBe(true);
  });

  it('serves index.html with the root mount point and title', async () => {
    vi.stubEnv('VITE_API_BASE_URL', '');
    // Port 0 avoids clashing with a dev server a developer already runs on 5173.
    const server = await createServer({
      configFile,
      root: webRoot,
      server: { port: 0, strictPort: false, hmr: false, watch: null },
      logLevel: 'silent',
    });
    try {
      await server.listen();
      const url = server.resolvedUrls?.local[0];
      expect(url).toBeDefined();
      const html = await (await fetch(url as string)).text();
      expect(html).toContain('<div id="root"></div>');
      expect(html).toContain('<title>MTG Stats</title>');
    } finally {
      await server.close();
    }
  }, 30_000);
});
