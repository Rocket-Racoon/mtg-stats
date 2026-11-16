// S-1.7.3 AC3: the guard stores no tokens itself. Token storage stays in E-2's expo-secure-store
// store (secureStorage.ts and secureTokenStore.ts); the guard files never touch a storage API.
import fs from 'node:fs';
import path from 'node:path';

const SRC = path.resolve(__dirname, '..');
// The E-2 store modules, which are expected to use expo-secure-store.
const STORE_MODULES = new Set(['secureStorage.ts', 'secureTokenStore.ts']);

function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
}

const sessionDir = path.join(SRC, 'session');
const rel = (f: string) => path.relative(SRC, f).replaceAll('\\', '/');
const guardFiles = [
  ...fs
    .readdirSync(sessionDir)
    .filter((f) => /\.tsx?$/.test(f) && !STORE_MODULES.has(f))
    .map((f) => path.join(sessionDir, f)),
  path.join(SRC, 'app', '_layout.tsx'),
  path.join(SRC, 'app', 'index.tsx'),
];

const FORBIDDEN: [string, RegExp][] = [
  ['expo-secure-store', /expo-secure-store/],
  ['AsyncStorage', /AsyncStorage|async-storage/],
  ['localStorage', /\blocalStorage\b/],
  ['sessionStorage', /\bsessionStorage\b/],
  ['fetch', /\bfetch\s*\(/],
];

describe('the guard files hold no token storage', () => {
  it('covers the layout, the index, and the session modules other than the E-2 store', () => {
    const names = guardFiles.map(rel);
    expect(names).toEqual(
      expect.arrayContaining([
        'app/_layout.tsx',
        'app/index.tsx',
        'session/paths.ts',
        'session/splash.ts',
        'session/SessionProvider.tsx',
      ]),
    );
    expect(names).not.toContain('session/secureStorage.ts');
  });

  it.each(guardFiles.map((f) => [rel(f), f] as const))(
    '%s uses no direct storage or fetch API',
    (_name, file) => {
      const code = stripComments(fs.readFileSync(file, 'utf8'));
      for (const [label, pattern] of FORBIDDEN) {
        expect({ label, found: pattern.test(code) }).toEqual({ label, found: false });
      }
    },
  );

  it('keeps expo-secure-store inside the E-2 store modules', () => {
    for (const f of STORE_MODULES) {
      expect(fs.existsSync(path.join(sessionDir, f))).toBe(true);
    }
    const users = fs
      .readdirSync(sessionDir)
      .filter((f) => /\.tsx?$/.test(f))
      .filter((f) =>
        /expo-secure-store/.test(stripComments(fs.readFileSync(path.join(sessionDir, f), 'utf8'))),
      );
    expect(users.length).toBeGreaterThan(0);
    expect(users.every((f) => STORE_MODULES.has(f))).toBe(true);
  });
});
