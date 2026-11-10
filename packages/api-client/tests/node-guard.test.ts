// AC4 support: the ESLint guard that keeps Node built-ins out of packages/api-client/src.
// (The Vite and Metro bundles themselves are checked by the verify gate, not by Vitest.)
import { spawnSync } from 'node:child_process';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const repoRoot = resolve(fileURLToPath(new URL('../../..', import.meta.url)));
const eslintBin = resolve(repoRoot, 'node_modules/eslint/bin/eslint.js');

function lint(code: string, file = 'packages/api-client/src/zz-guard-probe.ts') {
  const result = spawnSync(
    process.execPath,
    [eslintBin, '--stdin', '--stdin-filename', file, '--format', 'json'],
    { cwd: repoRoot, input: code, encoding: 'utf8' },
  );
  const parsed = JSON.parse(result.stdout) as {
    messages: { ruleId: string | null; message: string }[];
  }[];
  return parsed[0]!.messages.map((m) => m.ruleId);
}

describe('api-client Node-only guard', { timeout: 60_000 }, () => {
  it('flags a node: import under src', () => {
    const rules = lint("import { readFileSync } from 'node:fs';\nexport const x = readFileSync;\n");
    expect(rules).toContain('no-restricted-imports');
  });

  it('flags a bare Node built-in import under src', () => {
    const rules = lint("import { join } from 'path';\nexport const x = join;\n");
    expect(rules).toContain('no-restricted-imports');
  });

  it('flags the Node-only process global under src', () => {
    expect(lint('export const x = process.env;\n')).toContain('no-restricted-globals');
  });

  it('does not flag platform-neutral code', () => {
    const rules = lint('export const x = (u: string) => fetch(u);\n');
    expect(rules.filter((r) => r?.startsWith('no-restricted'))).toEqual([]);
  });
});
