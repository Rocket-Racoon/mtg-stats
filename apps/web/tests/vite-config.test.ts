// @vitest-environment node
import { mkdtempSync, existsSync, readdirSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve as resolvePath } from 'node:path';
import { fileURLToPath } from 'node:url';
import { build, createServer, loadConfigFromFile, type UserConfig } from 'vite';
import { afterEach, describe, expect, it, vi } from 'vitest';

const webRoot = fileURLToPath(new URL('..', import.meta.url));
const configFile = resolvePath(webRoot, 'vite.config.ts');

// Loaded with Vite's own loader, as `vite build` does.
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

  it('trims surrounding whitespace in the value that define injects', async () => {
    vi.stubEnv('VITE_API_BASE_URL', '  https://api.example.test/api/v1  ');
    const cfg = await resolve('build');
    expect(cfg.define?.['import.meta.env.VITE_API_BASE_URL']).toBe(
      JSON.stringify('https://api.example.test/api/v1'),
    );
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
      expect(html).toContain('/src/main.tsx');
    } finally {
      await server.close();
    }
  }, 30_000);
});

describe('vite build (slow, S-1.6.1 AC2 success path)', () => {
  it('SLOW: builds a production bundle with index.html when VITE_API_BASE_URL is set', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'https://api.example.test/api/v1');
    const outDir = mkdtempSync(join(tmpdir(), 'web-build-'));
    try {
      await build({
        configFile,
        root: webRoot,
        logLevel: 'silent',
        build: { outDir, emptyOutDir: true },
      });
      expect(existsSync(join(outDir, 'index.html'))).toBe(true);
      // The api-client must survive tree-shaking (S-1.5.2 AC4); drop this and the proof is lost.
      const assets = join(outDir, 'assets');
      const js = readdirSync(assets)
        .filter((f) => f.endsWith('.js'))
        .map((f) => readFileSync(join(assets, f), 'utf8'))
        .join('\n');
      expect(js).toContain('network_error');
    } finally {
      rmSync(outDir, { recursive: true, force: true });
    }
  }, 60_000);
});

describe('vite config: SPA fallback (S-1.6.2 AC4)', () => {
  it('resolves appType spa', async () => {
    vi.stubEnv('VITE_API_BASE_URL', '');
    const cfg = await resolve('serve', 'development');
    expect(cfg.appType).toBe('spa');
  });

  it('serves the index HTML for /decks and an unknown path, not a 404', async () => {
    vi.stubEnv('VITE_API_BASE_URL', '');
    const server = await createServer({
      configFile,
      root: webRoot,
      server: { port: 0, strictPort: false, hmr: false, watch: null },
      logLevel: 'silent',
    });
    try {
      await server.listen();
      const base = (server.resolvedUrls?.local[0] as string).replace(/\/$/, '');
      for (const path of ['/decks', '/nope/deeper']) {
        const res = await fetch(base + path, { headers: { accept: 'text/html' } });
        expect(res.status).toBe(200);
        const html = await res.text();
        expect(html).toContain('<div id="root"></div>');
        expect(html).toContain('/src/main.tsx');
      }
    } finally {
      await server.close();
    }
  }, 30_000);
});
