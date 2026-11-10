// AC2 proof: when a field disappears from a response schema and the client is regenerated,
// code that reads that field fails `tsc --noEmit`.
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterAll, describe, expect, it } from 'vitest';

const pkgRoot = resolve(fileURLToPath(new URL('..', import.meta.url)));
const tsc = createRequire(import.meta.url).resolve('typescript/bin/tsc');
const tmp = mkdtempSync(join(tmpdir(), 'api-client-ac2-'));

afterAll(() => rmSync(tmp, { recursive: true, force: true }));

type Spec = {
  components: {
    schemas: Record<string, { properties: Record<string, unknown>; required?: string[] }>;
  };
};

function generate(name: string, spec: Spec): string {
  const input = join(tmp, `${name}.openapi.json`);
  const output = join(tmp, `${name}.schema.d.ts`);
  writeFileSync(input, JSON.stringify(spec));
  const result = spawnSync(
    process.execPath,
    [join(pkgRoot, 'scripts/generate.mjs'), '--input', input, '--output', output],
    { encoding: 'utf8' },
  );
  expect(result.status, result.stderr).toBe(0);
  return output;
}

function typecheckConsumer(name: string, schemaPath: string) {
  const consumer = join(tmp, `${name}.consumer.ts`);
  const client = join(pkgRoot, 'src/client').replace(/\\/g, '/');
  writeFileSync(
    consumer,
    `import { createApiClient } from '${client}';
import type { paths } from './${name}.schema';

export async function read(): Promise<string> {
  const api = createApiClient<paths>({ baseUrl: 'https://api.test' });
  const live = await api.GET('/api/v1/health/live');
  return live.status;
}
`,
  );
  const tsconfig = join(tmp, `${name}.tsconfig.json`);
  writeFileSync(
    tsconfig,
    JSON.stringify({
      extends: join(pkgRoot, '../../tsconfig.base.json').replace(/\\/g, '/'),
      compilerOptions: { lib: ['ES2022', 'DOM'], typeRoots: [], types: [] },
      files: [consumer, schemaPath],
    }),
  );
  return spawnSync(process.execPath, [tsc, '--noEmit', '-p', tsconfig], { encoding: 'utf8' });
}

describe('regenerating the client after a schema change', () => {
  const spec = JSON.parse(readFileSync(join(pkgRoot, 'openapi.json'), 'utf8')) as Spec;

  it('type-checks a consumer that reads a field present in the schema', () => {
    const schema = generate('present', spec);
    const result = typecheckConsumer('present', schema);
    expect(result.stdout + result.stderr).toBe('');
    expect(result.status).toBe(0);
  }, 60_000);

  it('fails tsc for a consumer that reads a field removed from the schema', () => {
    const modified = structuredClone(spec);
    const live = modified.components.schemas.LiveResponse!;
    delete live.properties.status;
    live.required = (live.required ?? []).filter((key) => key !== 'status');
    const schema = generate('removed', modified);
    const result = typecheckConsumer('removed', schema);
    expect(result.status).not.toBe(0);
    expect(result.stdout).toMatch(/removed\.consumer\.ts.*error TS2339.*'status'/);
  }, 60_000);
});
