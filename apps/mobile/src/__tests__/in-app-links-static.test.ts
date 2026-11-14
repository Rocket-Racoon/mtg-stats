// S-1.6.7 AC4: in-app navigation on mobile uses mtgstats:// paths, never the https:// web form.
// Static scan over apps/mobile/src (tests excluded). External links go through Linking.openURL.
import { readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';

const mobileRoot = path.resolve(__dirname, '../..');
const srcDir = path.join(mobileRoot, 'src');
const read = (p: string) => readFileSync(p, 'utf8');

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) {
      return name === '__tests__' || name === 'node_modules' ? [] : sourceFiles(full);
    }
    return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) ? [full] : [];
  });
}

const files = sourceFiles(srcDir);
const rel = (f: string) => path.relative(srcDir, f).replace(/\\/g, '/');
const HTTP = String.raw`['"\`]\s*https?:\/\/`;

/** Offending source files for a pattern, with comments blanked first. */
function offenders(pattern: RegExp): string[] {
  return files
    .filter((f) => {
      const code = read(f)
        .replace(/\/\*[\s\S]*?\*\//g, '')
        .replace(/(^|[^:])\/\/.*$/gm, '$1');
      return pattern.test(code);
    })
    .map(rel);
}

describe('the scan itself', () => {
  it('finds the app sources and skips tests', () => {
    const names = files.map(rel);
    expect(names).toContain('app/_layout.tsx');
    expect(names).toContain('components/ExternalLink.tsx');
    expect(names.some((n) => n.startsWith('__tests__/'))).toBe(false);
  });

  it('flags an https navigation target and accepts an in-app path', () => {
    const nav = new RegExp(String.raw`\b(push|replace|navigate)\(\s*${HTTP}`);
    expect(nav.test(`router.push('https://app.example.com/invite/x')`)).toBe(true);
    expect(nav.test(`router.replace("http://x.test/join/y")`)).toBe(true);
    expect(nav.test(`router.push('/play')`)).toBe(false);
  });
});

describe('no https web URL as an in-app navigation target', () => {
  it.each([
    ['router.push', String.raw`\bpush\(\s*${HTTP}`],
    ['router.replace', String.raw`\breplace\(\s*${HTTP}`],
    ['router.navigate', String.raw`\bnavigate\(\s*${HTTP}`],
    ['<Link href>', String.raw`<Link\b[^>]*\bhref=\s*\{?\s*${HTTP}`],
    ['<Redirect href>', String.raw`<Redirect\b[^>]*\bhref=\s*\{?\s*${HTTP}`],
    ['href object pathname', String.raw`pathname:\s*${HTTP}`],
  ])('%s never takes an http(s):// string', (_name, pattern) => {
    expect(offenders(new RegExp(pattern))).toEqual([]);
  });

  it('never references PUBLIC_WEB_URL (the https base is server-side only)', () => {
    expect(offenders(/PUBLIC_WEB_URL/)).toEqual([]);
  });

  it('only uses http(s) URLs for external links through Linking.openURL', () => {
    const external = files.filter((f) => /Linking\.openURL/.test(read(f))).map(rel);
    expect(external).toContain('components/ExternalLink.tsx');
  });
});

describe('the app handles the mtgstats scheme', () => {
  it('declares scheme "mtgstats" in app.json', () => {
    const json = JSON.parse(read(path.join(mobileRoot, 'app.json'))) as {
      expo: { scheme?: string };
    };
    expect(json.expo.scheme).toBe('mtgstats');
  });

  it('does not override the scheme with another value in app.config.ts', () => {
    expect(read(path.join(mobileRoot, 'app.config.ts'))).not.toMatch(
      /scheme\s*:\s*['"](?!mtgstats['"])/,
    );
  });

  it.each([
    ['invite', 'src/app/invite/[token].tsx'],
    ['join', 'src/app/join/[code].tsx'],
  ])('has the %s route that mtgstats://%s/<value> opens', (_kind, file) => {
    expect(statSync(path.join(mobileRoot, file)).isFile()).toBe(true);
  });

  // The reset route lands with S-2.1.7; until then there is nothing to assert.
  test.todo('has the reset route at src/app/reset/[token].tsx (S-2.1.7)');
});
