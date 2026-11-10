// AC1 / AC2 support: `generate` writes schema.d.ts deterministically and `generate:check`
// is the drift gate (exit 1 when out of date, 0 when in sync).
import { spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterAll, describe, expect, it } from 'vitest';

const pkgRoot = resolve(fileURLToPath(new URL('..', import.meta.url)));
const script = join(pkgRoot, 'scripts/generate.mjs');
const spec = join(pkgRoot, 'openapi.json');
const tmp = mkdtempSync(join(tmpdir(), 'api-client-gen-'));

afterAll(() => rmSync(tmp, { recursive: true, force: true }));

function run(...args: string[]) {
  return spawnSync(process.execPath, [script, ...args], { encoding: 'utf8' });
}

const read = (file: string) => readFileSync(file, 'utf8').replace(/\r\n/g, '\n');

describe('generate', () => {
  it('writes the paths and components types', () => {
    const out = join(tmp, 'a.d.ts');
    const result = run('--input', spec, '--output', out);
    expect(result.status, result.stderr).toBe(0);
    const text = read(out);
    expect(text).toMatch(/export interface paths\b/);
    expect(text).toMatch(/export interface components\b/);
    expect(text).toContain('"/api/v1/health/live"');
  });

  it('is byte-identical when regenerated from the same spec', () => {
    const a = join(tmp, 'det-a.d.ts');
    const b = join(tmp, 'det-b.d.ts');
    expect(run('--input', spec, '--output', a).status).toBe(0);
    expect(run('--input', spec, '--output', b).status).toBe(0);
    expect(readFileSync(a).equals(readFileSync(b))).toBe(true);
  });

  it('creates missing output directories', () => {
    const out = join(tmp, 'nested/deeper/schema.d.ts');
    expect(run('--input', spec, '--output', out).status).toBe(0);
    expect(existsSync(out)).toBe(true);
  });

  it('matches the committed src/generated/schema.d.ts for the committed openapi.json', () => {
    const out = join(tmp, 'fresh.d.ts');
    expect(run('--input', spec, '--output', out).status).toBe(0);
    expect(read(out)).toBe(read(join(pkgRoot, 'src/generated/schema.d.ts')));
  });

  it('fails on an unknown argument', () => {
    expect(run('--bogus').status).not.toBe(0);
  });
});

describe('generate --check', () => {
  it('passes when the output is in sync', () => {
    const out = join(tmp, 'sync.d.ts');
    expect(run('--input', spec, '--output', out).status).toBe(0);
    const result = run('--input', spec, '--output', out, '--check');
    expect(result.status, result.stderr).toBe(0);
  });

  it('fails with a non-zero exit when the output has drifted, and does not rewrite it', () => {
    const out = join(tmp, 'drift.d.ts');
    writeFileSync(out, '// stale\n');
    const result = run('--input', spec, '--output', out, '--check');
    expect(result.status).toBe(1);
    expect(result.stderr).toContain('out of date');
    expect(readFileSync(out, 'utf8')).toBe('// stale\n');
  });

  it('fails when the output file does not exist', () => {
    const out = join(tmp, 'missing.d.ts');
    expect(run('--input', spec, '--output', out, '--check').status).toBe(1);
    expect(existsSync(out)).toBe(false);
  });

  it('fails when the spec changed after the last generation', () => {
    const input = join(tmp, 'changing.openapi.json');
    const out = join(tmp, 'changing.d.ts');
    const doc = JSON.parse(readFileSync(spec, 'utf8')) as {
      components: { schemas: Record<string, { properties: Record<string, unknown> }> };
    };
    writeFileSync(input, JSON.stringify(doc));
    expect(run('--input', input, '--output', out).status).toBe(0);
    doc.components.schemas.LiveResponse!.properties.extra = { type: 'string' };
    writeFileSync(input, JSON.stringify(doc));
    expect(run('--input', input, '--output', out, '--check').status).toBe(1);
  });

  it('passes for the committed schema against the committed spec', () => {
    const result = run('--check');
    expect(result.status, result.stderr).toBe(0);
  });
});
