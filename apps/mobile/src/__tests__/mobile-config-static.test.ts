import { mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import appConfig, { loadRootEnv, resolveBuildProfile } from '../../app.config';

const mobileRoot = path.resolve(__dirname, '../..');
const repoRoot = path.resolve(mobileRoot, '../..');
const read = (p: string) => readFileSync(p, 'utf8');

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) return name === 'node_modules' ? [] : sourceFiles(full);
    return /\.tsx?$/.test(name) ? [full] : [];
  });
}

describe('AC1: running the API for a physical phone', () => {
  const scripts = (
    JSON.parse(read(path.join(repoRoot, 'package.json'))) as { scripts: Record<string, string> }
  ).scripts;

  it('dev:api:lan is the dev:api command bound to 0.0.0.0 instead of 127.0.0.1', () => {
    expect(scripts['dev:api']).toContain('--host 127.0.0.1');
    expect(scripts['dev:api']).not.toContain('0.0.0.0');
    expect(scripts['dev:api:lan']).toContain('--host 0.0.0.0');
    expect(scripts['dev:api:lan']).toBe(
      scripts['dev:api']!.replace('--host 127.0.0.1', '--host 0.0.0.0'),
    );
  });

  it('README "Test on a phone" covers the LAN script, the variable, the emulator alias and the caveat', () => {
    const readme = read(path.join(repoRoot, 'README.md'));
    const start = readme.indexOf('Test on a phone');
    expect(start).toBeGreaterThan(-1);
    const rest = readme.slice(start);
    const next = rest.search(/\n#{2,3} /);
    const section = next === -1 ? rest : rest.slice(0, next);
    expect(section).toContain('dev:api:lan');
    expect(section).toContain('EXPO_PUBLIC_API_BASE_URL');
    expect(section).toContain('10.0.2.2');
    expect(section).toMatch(/exposes the dev API to everyone on your network/);
  });

  it('.env.example documents the variable', () => {
    expect(read(path.join(repoRoot, '.env.example'))).toMatch(/^EXPO_PUBLIC_API_BASE_URL=/m);
  });
});

describe('AC2: no silent fallback', () => {
  it('no non-test mobile source hardcodes a localhost address', () => {
    const offenders = sourceFiles(path.join(mobileRoot, 'src'))
      .filter((f) => !f.includes('__tests__'))
      .filter((f) => /localhost|127\.0\.0\.1/.test(read(f)));
    expect(offenders).toEqual([]);
    expect(read(path.join(mobileRoot, 'app.config.ts'))).not.toMatch(/localhost/);
  });
});

describe('T-1.7.4.1: app.config.ts', () => {
  it.each([
    [{ EAS_BUILD_PROFILE: 'preview', APP_PROFILE: 'production' }, 'preview'],
    [{ EAS_BUILD_PROFILE: '', APP_PROFILE: 'preview' }, 'preview'],
    [{ EAS_BUILD_PROFILE: 'PRODUCTION' }, 'production'],
    [{ EAS_BUILD_PROFILE: 'Preview' }, 'preview'],
    [{ EAS_BUILD_PROFILE: 'bogus', APP_PROFILE: 'production' }, 'development'],
    [{ APP_PROFILE: ' preview ' }, 'preview'],
    [{}, 'development'],
  ])('resolves %j to %s', (env, expected) => {
    expect(resolveBuildProfile(env)).toBe(expected);
  });

  it('keeps every app.json value (name, slug, scheme, package, version, plugins)', () => {
    const { expo } = JSON.parse(read(path.join(mobileRoot, 'app.json'))) as {
      expo: Record<string, unknown>;
    };
    const config = appConfig({ config: expo, projectRoot: mobileRoot } as never);
    expect(config).toMatchObject(expo);
    expect(config.name).toBe('MTG Stats');
    expect(config.slug).toBe('mtg-stats');
    expect(config.scheme).toBe('mtgstats');
    expect(config.android?.package).toBe('app.mtgstats');
    expect(config.version).toBe('0.0.0');
    expect(config.plugins).toEqual(['expo-router', 'expo-secure-store']);
    expect(config.extra?.buildProfile).toMatch(/^(development|preview|production)$/);
  });
});

describe('loadRootEnv precedence', () => {
  let dir: string;
  beforeEach(() => {
    dir = mkdtempSync(path.join(tmpdir(), 'root-env-extra-'));
  });
  afterEach(() => rmSync(dir, { recursive: true, force: true }));

  it('never overrides a value already set, and leaves other keys alone', () => {
    writeFileSync(
      path.join(dir, '.env'),
      'EXPO_PUBLIC_API_BASE_URL=http://root/api/v1\nAPP_PROFILE=production\n',
    );
    const env: Record<string, string | undefined> = {
      EXPO_PUBLIC_API_BASE_URL: 'http://shell/api/v1',
    };
    loadRootEnv(env, dir);
    expect(env).toEqual({ EXPO_PUBLIC_API_BASE_URL: 'http://shell/api/v1' });
  });

  it('copies only EXPO_PUBLIC_API_BASE_URL and tolerates a missing root .env', () => {
    const env: Record<string, string | undefined> = {};
    expect(() => loadRootEnv(env, path.join(dir, 'does-not-exist'))).not.toThrow();
    expect(env).toEqual({});
  });
});

describe('TanStack is reached only through @mtg-stats/api-client', () => {
  it('apps/mobile/package.json does not depend on @tanstack/react-query', () => {
    const pkg = JSON.parse(read(path.join(mobileRoot, 'package.json'))) as Record<
      string,
      Record<string, string> | undefined
    >;
    for (const key of ['dependencies', 'devDependencies', 'peerDependencies']) {
      expect(Object.keys(pkg[key] ?? {})).not.toContain('@tanstack/react-query');
    }
  });

  it('no mobile source imports @tanstack/react-query directly', () => {
    const offenders = sourceFiles(path.join(mobileRoot, 'src'))
      .filter((f) => !f.endsWith('mobile-config-static.test.ts'))
      .filter((f) => read(f).includes('@tanstack/react-query'));
    expect(offenders).toEqual([]);
  });

  it('the Play screen no longer has a side-effect import of the api module', () => {
    const play = read(path.join(mobileRoot, 'src/app/(tabs)/play/index.tsx'));
    expect(play).not.toMatch(/import\s+['"][./]*\/api['"]/);
  });
});
