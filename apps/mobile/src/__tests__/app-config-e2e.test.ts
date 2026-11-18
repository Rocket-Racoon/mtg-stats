import appConfig, { isE2eBuild, withE2eCleartext } from '../../app.config';

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

  it('leaves plugins exactly as passed in when MTG_E2E_BUILD is unset', () => {
    delete process.env.MTG_E2E_BUILD;
    expect(build().plugins).toEqual([existingPlugin]);
    expect(build().plugins).not.toContain(withE2eCleartext);
  });

  it('leaves plugins exactly as passed in when MTG_E2E_BUILD is 0', () => {
    process.env.MTG_E2E_BUILD = '0';
    expect(build().plugins).toEqual([existingPlugin]);
    expect(build().plugins).not.toContain(withE2eCleartext);
  });

  it('adds withE2eCleartext to plugins when MTG_E2E_BUILD is 1', () => {
    process.env.MTG_E2E_BUILD = '1';
    const plugins = build().plugins ?? [];
    expect(plugins).toContain(withE2eCleartext);
    expect(plugins).toContainEqual(existingPlugin);
  });
});

describe('withE2eCleartext (S-1.8.6)', () => {
  it('sets android:usesCleartextTraffic="true" on <application>', async () => {
    const config = withE2eCleartext({ name: 'MTG Stats', slug: 'mtg-stats' });
    const mod = (
      config as unknown as {
        mods: { android: { manifest: (c: unknown) => Promise<{ modResults: unknown }> } };
      }
    ).mods.android.manifest;
    const manifest = {
      manifest: { application: [{ $: { 'android:name': '.MainApplication' } }] },
    };
    const result = await mod({
      ...config,
      modRequest: { projectRoot: '.', platformProjectRoot: '.', modName: 'manifest' },
      modResults: manifest,
    });
    const application = (
      result.modResults as { manifest: { application: { $: Record<string, string> }[] } }
    ).manifest.application[0];
    expect(application?.$['android:usesCleartextTraffic']).toBe('true');
    expect(application?.$['android:name']).toBe('.MainApplication');
  });
});
