/**
 * Repo-level guards for S-1.1.3 (pnpm workspaces). They live in game-core only because it is a
 * workspace where Vitest already runs; the repo root is found by walking up from the cwd.
 * Node built-ins are loaded through a variable specifier because no workspace depends on @types/node.
 */
import { describe, expect, it } from 'vitest';
import ts from 'typescript';
import { ESLint } from 'eslint';
import * as prettier from 'prettier';
import prettierConfig from 'eslint-config-prettier';

interface NodeFs {
  existsSync(path: string): boolean;
  readFileSync(path: string, encoding: 'utf8'): string;
  readdirSync(path: string): string[];
}
interface NodePath {
  join(...parts: string[]): string;
  dirname(path: string): string;
}

const nodeFsId = 'node:fs';
const nodePathId = 'node:path';
const fs = (await import(/* @vite-ignore */ nodeFsId)) as NodeFs;
const path = (await import(/* @vite-ignore */ nodePathId)) as NodePath;

function findRoot(): string {
  const proc = (globalThis as unknown as { process: { cwd(): string } }).process;
  let dir = proc.cwd();
  while (!fs.existsSync(path.join(dir, 'pnpm-workspace.yaml'))) {
    const parent = path.dirname(dir);
    if (parent === dir) throw new Error('pnpm-workspace.yaml not found above cwd');
    dir = parent;
  }
  return dir;
}

const root = findRoot();
const read = (...p: string[]): string => fs.readFileSync(path.join(root, ...p), 'utf8');
const readJson = (...p: string[]): Record<string, unknown> =>
  JSON.parse(read(...p)) as Record<string, unknown>;

function workspaceDirs(): string[] {
  const patterns = read('pnpm-workspace.yaml')
    .split(/\r?\n/)
    .map((l) => /^\s*-\s*['"]?([^'"#\s]+)['"]?/.exec(l)?.[1])
    .filter((p): p is string => p !== undefined);
  const dirs: string[] = [];
  for (const pattern of patterns) {
    if (pattern.endsWith('/*')) {
      const base = pattern.slice(0, -2);
      for (const name of fs.readdirSync(path.join(root, base))) {
        if (fs.existsSync(path.join(root, base, name, 'package.json')))
          dirs.push(`${base}/${name}`);
      }
    } else {
      dirs.push(pattern);
    }
  }
  return dirs;
}

function parseTsconfig(text: string, file: string): Record<string, unknown> {
  const { config, error } = ts.parseConfigFileTextToJson(file, text);
  expect(error, `${file} must parse`).toBeUndefined();
  return config as Record<string, unknown>;
}

const dirs = workspaceDirs();

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
    const base = parseTsconfig(read('tsconfig.base.json'), 'tsconfig.base.json');
    const opts = base.compilerOptions as Record<string, unknown>;
    expect(opts.strict).toBe(true);
    expect(opts.noUncheckedIndexedAccess).toBe(true);
  });

  it.each(dirs)('%s tsconfig extends the base and keeps both options on', (dir) => {
    const file = `${dir}/tsconfig.json`;
    const cfg = parseTsconfig(read(file), file);
    const extendsPath = cfg.extends as string;
    const resolved = path.join(root, dir, extendsPath);
    expect(typeof extendsPath).toBe('string');
    expect(resolved).toBe(path.join(root, 'tsconfig.base.json'));
    const opts = (cfg.compilerOptions ?? {}) as Record<string, unknown>;
    expect(opts.strict).not.toBe(false);
    expect(opts.noUncheckedIndexedAccess).not.toBe(false);
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

// ESLint's first run loads the typescript-eslint program and is slow on cold caches.
describe('eslint and prettier separation (criterion 5)', { timeout: 90_000 }, () => {
  const filePath = path.join(root, 'apps/web/src/format-snippet.ts');
  // Valid code that only breaks Prettier's rules: double quotes, odd spacing, missing semicolons.
  const snippet = 'export const   value = {a:1,\n   b:"two"}\n';

  it('eslint reports nothing for a formatting-only violation', async () => {
    const eslint = new ESLint({
      cwd: root,
      overrideConfigFile: path.join(root, 'eslint.config.js'),
    });
    const results = await eslint.lintText(snippet, { filePath });
    expect(results.flatMap((r) => r.messages)).toEqual([]);
  });

  it('prettier reports the same snippet as unformatted', async () => {
    const options = await prettier.resolveConfig(filePath);
    expect(await prettier.check(snippet, { ...options, filepath: filePath })).toBe(false);
  });

  it('root eslint config switches off every rule that eslint-config-prettier disables', async () => {
    const eslint = new ESLint({
      cwd: root,
      overrideConfigFile: path.join(root, 'eslint.config.js'),
    });
    const config = (await eslint.calculateConfigForFile(filePath)) as {
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
  });
});
