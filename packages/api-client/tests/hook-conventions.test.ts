// AC3: hooks are hand-written (decision A4): no hook-generation tool, and every hooks module is
// built on the shared API client and the key factory.
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const root = fileURLToPath(new URL('..', import.meta.url));
const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8')) as Record<
  string,
  Record<string, string> | undefined
>;
const banned =
  /orval|@hey-api|openapi-react-query|kubb|swagger-typescript-api|rtk-query-codegen|openapi-codegen/;

describe('hook conventions', () => {
  it.each(['dependencies', 'devDependencies', 'peerDependencies', 'optionalDependencies'])(
    'has no hook generator in %s',
    (field) => {
      expect(Object.keys(pkg[field] ?? {}).filter((d) => banned.test(d))).toEqual([]);
    },
  );

  it('has no hook generator in scripts', () => {
    expect(Object.values(pkg['scripts'] ?? {}).join('\n')).not.toMatch(banned);
  });

  const hooksDir = join(root, 'src', 'hooks');
  const hookFiles = readdirSync(hooksDir).filter(
    (f: string) => f.endsWith('.ts') && !f.endsWith('.test.ts'),
  );

  it('has the system hooks module', () => {
    expect(hookFiles).toContain('system.ts');
  });

  it.each(hookFiles)('hooks/%s uses useApiClient and the key factory, not raw fetch', (file) => {
    const src = readFileSync(join(hooksDir, file), 'utf8');
    expect(src).toMatch(/useApiClient/);
    expect(src).toMatch(/queryKey\(/);
    expect(src).not.toMatch(/\bfetch\(/);
  });
});
