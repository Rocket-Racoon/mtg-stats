/**
 * S-1.5.5 acceptance tests for scripts/client-drift-check.js. The real API export is replaced by
 * a fake exporter (--export-cmd) that copies a fixture spec, so no uv, database or API is needed.
 */
import { spawnSync } from 'node:child_process';
import { copyFileSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

const root = join(import.meta.dirname, '..', '..');
const script = join(root, 'scripts', 'client-drift-check.js');
const generate = join(root, 'packages', 'api-client', 'scripts', 'generate.mjs');
const realSpec = join(root, 'packages', 'api-client', 'openapi.json');

let dir: string;
let apiSpec: string; // what the "API" exports
let committedSpec: string;
let committedSchema: string;
let exporter: string;
let failing: string;

function generateSchema(spec: string, out: string): void {
  const r = spawnSync(process.execPath, [generate, '--input', spec, '--output', out], {
    encoding: 'utf8',
  });
  if (r.status !== 0) throw new Error(r.stderr);
}

/** A spec with one extra path: the "API schema changed" scenario. */
function changedSpec(): string {
  const spec = JSON.parse(readFileSync(realSpec, 'utf8')) as { paths: Record<string, unknown> };
  spec.paths['/api/v1/drift-probe'] = {
    get: {
      operationId: 'driftProbe',
      responses: { '200': { description: 'ok' } },
    },
  };
  return JSON.stringify(spec, null, 2) + '\n';
}

const slashed = (p: string): string => p.replaceAll('\\', '/');

function check(exportCmd?: string[]) {
  const r = spawnSync(
    process.execPath,
    [
      script,
      '--spec',
      committedSpec,
      '--schema',
      committedSchema,
      '--export-cmd',
      JSON.stringify(exportCmd ?? [process.execPath, exporter, apiSpec]),
    ],
    { encoding: 'utf8', cwd: root },
  );
  return { code: r.status, out: r.stdout, err: r.stderr };
}

beforeAll(() => {
  dir = mkdtempSync(join(tmpdir(), 'drift-test-'));
  apiSpec = join(dir, 'api-spec.json');
  committedSpec = join(dir, 'openapi.json');
  committedSchema = join(dir, 'schema.d.ts');
  exporter = join(dir, 'fake-export.mjs');
  failing = join(dir, 'failing-export.mjs');
  writeFileSync(failing, 'process.exit(7);\n');
  writeFileSync(
    exporter,
    [
      "import { copyFileSync } from 'node:fs';",
      'const args = process.argv.slice(2);',
      "copyFileSync(args[0], args[args.indexOf('--output') + 1]);",
      '',
    ].join('\n'),
  );
}, 30_000);

afterAll(() => rmSync(dir, { recursive: true, force: true }));

describe('client drift check', () => {
  it('exits 0 when the committed client was freshly regenerated', () => {
    copyFileSync(realSpec, apiSpec);
    copyFileSync(realSpec, committedSpec);
    generateSchema(committedSpec, committedSchema);
    const r = check();
    expect(r.code).toBe(0);
    expect(r.out).toContain('up to date');
  }, 60_000);

  it('exits 1 and lists both files when the API changed without regenerating', () => {
    writeFileSync(apiSpec, changedSpec());
    const r = check();
    expect(r.code).toBe(1);
    expect(r.err).toContain(slashed(committedSpec));
    expect(r.err).toContain(slashed(committedSchema));
    expect(r.err).toContain('pnpm client:generate');
  }, 60_000);

  it('lists only the schema when just the generated types are stale', () => {
    writeFileSync(apiSpec, changedSpec());
    copyFileSync(apiSpec, committedSpec);
    const r = check();
    expect(r.code).toBe(1);
    expect(r.err).toContain(slashed(committedSchema));
    expect(r.err).not.toContain(slashed(committedSpec));
  }, 60_000);

  it('treats CRLF line endings in committed files as equal', () => {
    copyFileSync(realSpec, apiSpec);
    writeFileSync(
      committedSpec,
      readFileSync(realSpec, 'utf8').replace(/\r\n/g, '\n').replace(/\n/g, '\r\n'),
    );
    generateSchema(realSpec, committedSchema);
    expect(check().code).toBe(0);
  }, 60_000);

  it('exits 2 with a message when the export fails', () => {
    const r = check([process.execPath, failing]);
    expect(r.code).toBe(2);
    expect(r.err).toContain('OpenAPI export failed (exit 7)');
  }, 60_000);

  it('exits 2 when the export command cannot start', () => {
    const r = check(['definitely-not-a-real-command-xyz']);
    expect(r.code).toBe(2);
    expect(r.err).toContain('failed to start');
  }, 60_000);

  it('exits 2 on an unknown argument', () => {
    const r = spawnSync(process.execPath, [script, '--bogus'], { encoding: 'utf8' });
    expect(r.status).toBe(2);
  });

  it('never modifies the committed files', () => {
    copyFileSync(realSpec, apiSpec);
    writeFileSync(committedSpec, changedSpec());
    const before = readFileSync(committedSpec, 'utf8');
    check();
    expect(readFileSync(committedSpec, 'utf8')).toBe(before);
  }, 60_000);
});
