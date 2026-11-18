import appConfig, { isE2eBuild, withE2eCleartext } from '../../app.config';

type Application = { $: Record<string, string> };
type ManifestMod = (c: unknown) => Promise<{ modResults: unknown }>;

/** The Android manifest mod attached to a config, or undefined when there is none. */
const manifestMod = (config: unknown): ManifestMod | undefined =>
  (config as { mods?: { android?: { manifest?: ManifestMod } } }).mods?.android?.manifest;

/** Runs the config's manifest mod on a minimal manifest and returns the first <application>. */
async function runManifestMod(config: unknown): Promise<Application | undefined> {
  const mod = manifestMod(config);
  if (!mod) return undefined;
  const result = await mod({
    ...(config as object),
    modRequest: { projectRoot: '.', platformProjectRoot: '.', modName: 'manifest' },
    modResults: { manifest: { application: [{ $: { 'android:name': '.MainApplication' } }] } },
  });
  return (result.modResults as { manifest: { application: Application[] } }).manifest
    .application[0];
}

describe('isE2eBuild (S-1.8.6)', () => {
  it.each([
    ['1', true],
    ['0', false],
    ['true', false],
    ['', false],
    [' 1', false],
    ['11', false],
    [undefined, false],
  ])('MTG_E2E_BUILD=%j is %s', (value, expected) => {
    expect(isE2eBuild({ MTG_E2E_BUILD: value })).toBe(expected);
  });

  it('is false when the variable is absent', () => {
    expect(isE2eBuild({})).toBe(false);
  });
});

describe('app.config.ts e2e cleartext switch (S-1.8.6)', () => {
  const saved = process.env.MTG_E2E_BUILD;
  afterEach(() => {
    if (saved === undefined) delete process.env.MTG_E2E_BUILD;
    else process.env.MTG_E2E_BUILD = saved;
  });

  const base = { name: 'MTG Stats', slug: 'mtg-stats', scheme: 'mtgstats', version: '0.0.0' };
  const existingPlugin = ['expo-router'] as [string];
  const build = () =>
    appConfig({ config: { ...base, plugins: [existingPlugin] }, projectRoot: '.' } as never);

  it('has no Android manifest mod and keeps plugins as passed in when MTG_E2E_BUILD is unset', async () => {
    delete process.env.MTG_E2E_BUILD;
    const config = build();
    expect(manifestMod(config)).toBeUndefined();
    expect(config.plugins).toEqual([existingPlugin]);
    expect((await runManifestMod(config))?.$['android:usesCleartextTraffic']).toBeUndefined();
  });

  it('has no Android manifest mod and keeps plugins as passed in when MTG_E2E_BUILD is 0', async () => {
    process.env.MTG_E2E_BUILD = '0';
    const config = build();
    expect(manifestMod(config)).toBeUndefined();
    expect(config.plugins).toEqual([existingPlugin]);
    expect((await runManifestMod(config))?.$['android:usesCleartextTraffic']).toBeUndefined();
  });

  it('sets usesCleartextTraffic on the first <application> when MTG_E2E_BUILD is 1', async () => {
    process.env.MTG_E2E_BUILD = '1';
    const config = build();
    expect(manifestMod(config)).toBeDefined();
    const application = await runManifestMod(config);
    expect(application?.$['android:usesCleartextTraffic']).toBe('true');
    expect(application?.$['android:name']).toBe('.MainApplication');
  });
});

describe('withE2eCleartext (S-1.8.6)', () => {
  it('sets android:usesCleartextTraffic="true" on <application>', async () => {
    const application = await runManifestMod(
      withE2eCleartext({ name: 'MTG Stats', slug: 'mtg-stats' }),
    );
    expect(application?.$['android:usesCleartextTraffic']).toBe('true');
    expect(application?.$['android:name']).toBe('.MainApplication');
  });
});
