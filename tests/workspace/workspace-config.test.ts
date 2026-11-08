/**
 * Repo-level guards for S-1.1.3 (pnpm workspaces): toolchain pins, workspace links, tsconfig
 * strictness, root script wiring (criterion 4) and the ESLint/Prettier split (criterion 5).
 */
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { describe, expect, it } from 'vitest';
import ts from 'typescript';
import { ESLint } from 'eslint';
import * as prettier from 'prettier';
import prettierConfig from 'eslint-config-prettier';

const root = join(import.meta.dirname, '..', '..');
const read = (...p: string[]): string => readFileSync(join(root, ...p), 'utf8');
const readJson = (...p: string[]): Record<string, unknown> =>
  JSON.parse(read(...p)) as Record<string, unknown>;

/** Items under the `packages:` key only, expanded for `dir/*` globs. */
function workspaceDirs(): string[] {
  const lines = read('pnpm-workspace.yaml').split(/\r?\n/);
  const patterns: string[] = [];
  let inPackages = false;
  for (const line of lines) {
    if (/^packages\s*:/.test(line)) {
      inPackages = true;
    } else if (/^\S/.test(line)) {
      inPackages = false;
    } else if (inPackages) {
      const m = /^\s*-\s*['"]?([^'"#\s]+)['"]?/.exec(line);
      if (m?.[1]) patterns.push(m[1]);
    }
  }
  const dirs: string[] = [];
  for (const pattern of patterns) {
    if (pattern.endsWith('/*')) {
      const base = pattern.slice(0, -2);
      for (const name of readdirSync(join(root, base))) {
        if (existsSync(join(root, base, name, 'package.json'))) dirs.push(`${base}/${name}`);
      }
    } else {
      dirs.push(pattern);
    }
  }
  return dirs;
}

function parseTsconfig(file: string): Record<string, unknown> {
  const { config, error } = ts.parseConfigFileTextToJson(file, read(file));
  expect(error, `${file} must parse`).toBeUndefined();
  return config as Record<string, unknown>;
}

const disabledStrictFlags = (opts: Record<string, unknown>): string[] =>
  Object.entries(opts)
    .filter(([k, v]) => k.startsWith('strict') && v === false)
    .map(([k]) => k);

const dirs = workspaceDirs();
const tsconfigFiles = ['tsconfig.json', ...dirs.map((d) => `${d}/tsconfig.json`)];

describe('workspace discovery', () => {
  it('finds web, mobile and the three shared packages', () => {
    expect(dirs).toEqual(
      expect.arrayContaining([
        'apps/web',
        'apps/mobile',
        'packages/game-core',
        'packages/api-client',
        'packages/card-utils',
      ]),
    );
  });
});

describe('tsconfig strictness (criterion 3)', () => {
  it('base config sets strict and noUncheckedIndexedAccess to true', () => {
    const opts = parseTsconfig('tsconfig.base.json').compilerOptions as Record<string, unknown>;
    expect(opts.strict).toBe(true);
    expect(opts.noUncheckedIndexedAccess).toBe(true);
  });

  it('base config disables no strict* sub-flag', () => {
    const opts = parseTsconfig('tsconfig.base.json').compilerOptions as Record<string, unknown>;
    expect(disabledStrictFlags(opts)).toEqual([]);
  });

  it.each(tsconfigFiles)('%s extends the base and keeps strictness on', (file) => {
    const cfg = parseTsconfig(file);
    expect(typeof cfg.extends).toBe('string');
    expect(join(root, dirname(file), cfg.extends as string)).toBe(join(root, 'tsconfig.base.json'));
    const opts = (cfg.compilerOptions ?? {}) as Record<string, unknown>;
    expect(opts.strict).not.toBe(false);
    expect(opts.noUncheckedIndexedAccess).not.toBe(false);
    expect(disabledStrictFlags(opts)).toEqual([]);
  });
});

describe('pinned toolchain (criterion 1)', () => {
  const pkg = readJson('package.json');
  const engines = pkg.engines as Record<string, string>;

  it('pins packageManager to an exact pnpm semver', () => {
    expect(pkg.packageManager).toMatch(/^pnpm@\d+\.\d+\.\d+$/);
  });

  it('declares an engines.node range', () => {
    expect(engines.node).toMatch(/^>=\d+(\.\d+)*\s+<\d+(\.\d+)*$/);
  });

  it('keeps .nvmrc inside the Node range allowed by engines.node', () => {
    const [, lower, upper] = /^>=(\d+)[\d.]*\s+<(\d+)/.exec(engines.node as string) ?? [];
    const nvmMajor = Number(read('.nvmrc').trim().replace(/^v/, '').split('.')[0]);
    expect(nvmMajor).toBeGreaterThanOrEqual(Number(lower));
    expect(nvmMajor).toBeLessThan(Number(upper));
  });
});

describe('workspace links (criterion 2)', () => {
  it.each(['apps/web', 'apps/mobile'])(
    '%s depends on all shared packages via workspace:*',
    (dir) => {
      const deps = readJson(dir, 'package.json').dependencies as Record<string, string>;
      for (const name of ['game-core', 'api-client', 'card-utils']) {
        expect(deps[`@mtg-stats/${name}`]).toBe('workspace:*');
      }
    },
  );
});

describe('root script wiring (criterion 4)', () => {
  // `pnpm -r <script>` silently skips a workspace without the script, so each must define all five.
  const required = ['lint', 'format', 'format:check', 'typecheck', 'test'];

  it.each(dirs)('%s defines every required script', (dir) => {
    const scripts = (readJson(dir, 'package.json').scripts ?? {}) as Record<string, string>;
    expect(required.filter((name) => typeof scripts[name] !== 'string')).toEqual([]);
  });

  const rootScripts = (readJson('package.json').scripts ?? {}) as Record<string, string>;

  it.each(['lint', 'format:check', 'typecheck', 'test'])(
    'root %s runs the recursive script and cannot swallow failures',
    (name) => {
      const script = rootScripts[name] ?? '';
      expect(script.startsWith(`pnpm -r ${name}`)).toBe(true);
      for (const banned of ['--no-bail', '|| true', '--silent']) {
        expect(script).not.toContain(banned);
      }
    },
  );
});

describe('eslint and prettier separation (criterion 5)', () => {
  const filePath = join(root, 'apps/web/src/format-snippet.ts');
  // Valid code that only breaks Prettier rules: double quotes, odd spacing, missing semicolons.
  const snippet = 'export const   value = {a:1,\n   b:"two"}\n';
  const newEslint = (): ESLint =>
    new ESLint({ cwd: root, overrideConfigFile: join(root, 'eslint.config.js') });

  // ESLint's first run loads the typescript-eslint program and is slow on cold caches.
  it('eslint reports nothing for a formatting-only violation', { timeout: 90_000 }, async () => {
    const results = await newEslint().lintText(snippet, { filePath });
    expect(results.flatMap((r) => r.messages)).toEqual([]);
  });

  it('prettier reports the same snippet as unformatted', async () => {
    const options = await prettier.resolveConfig(filePath);
    expect(await prettier.check(snippet, { ...options, filepath: filePath })).toBe(false);
  });

  it(
    'root eslint config switches off every rule that eslint-config-prettier disables',
    { timeout: 90_000 },
    async () => {
      const config = (await newEslint().calculateConfigForFile(filePath)) as {
        rules: Record<string, unknown>;
      };
      const isOff = (v: unknown): boolean => {
        const level = Array.isArray(v) ? v[0] : v;
        return level === undefined || level === 0 || level === 'off';
      };
      const disabled = Object.entries(prettierConfig.rules ?? {}).filter(([, v]) => isOff(v));
      expect(disabled.length).toBeGreaterThan(10);
      const stillOn = disabled.map(([name]) => name).filter((n) => !isOff(config.rules[n]));
      expect(stillOn).toEqual([]);
    },
  );
});
