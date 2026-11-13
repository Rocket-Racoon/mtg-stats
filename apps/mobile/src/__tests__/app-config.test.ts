import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import appConfig, { loadRootEnv, resolveBuildProfile } from '../../app.config';

describe('resolveBuildProfile (S-1.7.4 T-1.7.4.1)', () => {
  it('defaults to development', () => {
    expect(resolveBuildProfile({})).toBe('development');
  });

  it('uses APP_PROFILE when EAS_BUILD_PROFILE is absent', () => {
    expect(resolveBuildProfile({ APP_PROFILE: 'preview' })).toBe('preview');
  });

  it('prefers EAS_BUILD_PROFILE over APP_PROFILE', () => {
    expect(resolveBuildProfile({ EAS_BUILD_PROFILE: 'production', APP_PROFILE: 'preview' })).toBe(
      'production',
    );
  });

  it('is case-insensitive and falls back to development for unknown values', () => {
    expect(resolveBuildProfile({ APP_PROFILE: 'Production' })).toBe('production');
    expect(resolveBuildProfile({ APP_PROFILE: 'staging' })).toBe('development');
  });
});

describe('app.config.ts', () => {
  const base = { name: 'MTG Stats', slug: 'mtg-stats', scheme: 'mtgstats', version: '0.0.0' };

  it('keeps the app.json values and exposes the profile through extra', () => {
    const saved = process.env.EAS_BUILD_PROFILE;
    process.env.EAS_BUILD_PROFILE = 'preview';
    try {
      const config = appConfig({
        config: { ...base, extra: { keep: 1 } },
        projectRoot: '.',
      } as never);
      expect(config).toMatchObject({ ...base, extra: { keep: 1, buildProfile: 'preview' } });
    } finally {
      if (saved === undefined) delete process.env.EAS_BUILD_PROFILE;
      else process.env.EAS_BUILD_PROFILE = saved;
    }
  });
});

describe('loadRootEnv (root .env reaches mobile)', () => {
  let dir: string;
  beforeEach(() => {
    dir = mkdtempSync(path.join(tmpdir(), 'root-env-'));
  });
  afterEach(() => rmSync(dir, { recursive: true, force: true }));

  const write = (text: string) => writeFileSync(path.join(dir, '.env'), text);

  it('copies the variable from the root .env when it is unset', () => {
    write('POSTGRES_USER=x\nEXPO_PUBLIC_API_BASE_URL=http://192.168.1.20:8000/api/v1\n');
    const env: Record<string, string | undefined> = {};
    loadRootEnv(env, dir);
    expect(env.EXPO_PUBLIC_API_BASE_URL).toBe('http://192.168.1.20:8000/api/v1');
    expect(env.POSTGRES_USER).toBeUndefined();
  });

  it('keeps a value already set (shell or apps/mobile/.env wins)', () => {
    write('EXPO_PUBLIC_API_BASE_URL=http://root/api/v1\n');
    const env: Record<string, string | undefined> = {
      EXPO_PUBLIC_API_BASE_URL: 'http://shell/api/v1',
    };
    loadRootEnv(env, dir);
    expect(env.EXPO_PUBLIC_API_BASE_URL).toBe('http://shell/api/v1');
  });

  it('does nothing without a root .env', () => {
    const env: Record<string, string | undefined> = {};
    loadRootEnv(env, dir);
    expect(env.EXPO_PUBLIC_API_BASE_URL).toBeUndefined();
  });
});
