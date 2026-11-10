/**
 * S-1.5.5 acceptance tests, second pass. Uses the REAL API exporter (uv) against the committed
 * client for the end-to-end ACs (about 8 s per run), and a fake exporter for failure paths.
 */
import { spawnSync } from 'node:child_process';
import {
  copyFileSync,
  existsSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

const root = join(import.meta.dirname, '..', '..');
const script = join(root, 'scripts', 'client-drift-check.js');
const generate = join(root, 'packages', 'api-client', 'scripts', 'generate.mjs');
const realSpec = join(root, 'packages', 'api-client', 'openapi.json');
const realSchema = join(root, 'packages', 'api-client', 'src', 'generated', 'schema.d.ts');
const slashed = (p: string): string => p.replaceAll('\\', '/');
const TIMEOUT = 120_000;
const HINT = 'Run `pnpm client:generate` and commit the result.';

let dir: string;
/** Private temp dir handed to every spawned run, so temp-dir assertions ignore other processes. */
let priv: string;

function run(args: string[], env: Record<string, string> = {}) {
  const r = spawnSync(process.execPath, [script, ...args], {
    encoding: 'utf8',
    cwd: root,
    env: { ...process.env, TMPDIR: priv, TMP: priv, TEMP: priv, ...env },
  });
  return { code: r.status, out: r.stdout, err: r.stderr };
}

/** Writes a fake exporter that writes `body` (or env FAKE_BODY) to its --output path. */
function writeExporter(name: string, body: string | null): string {
  const p = join(dir, name);
  const content = body === null ? 'process.env.FAKE_BODY ?? ""' : JSON.stringify(body);
  writeFileSync(
    p,
    [
      "import { writeFileSync } from 'node:fs';",
      'const a = process.argv.slice(2);',
      `writeFileSync(a[a.indexOf('--output') + 1], ${content});`,
      '',
    ].join('\n'),
  );
  return p;
}

/** An exporter script that exits with `code` (node -e cannot be used: --output is appended). */
function exitScript(code: number): string {
  const p = join(dir, `exit-${code}.mjs`);
  writeFileSync(p, `process.exit(${code});\n`);
  return p;
}

const otherSpecBody = JSON.stringify({
  openapi: '3.1.0',
  info: { title: 'x', version: '1' },
  paths: {},
});

/** Copy of the committed spec with one response schema property renamed (a changed API schema). */
function staleSpec(path: string): void {
  const spec = JSON.parse(readFileSync(realSpec, 'utf8')) as {
    components: { schemas: Record<string, { properties?: Record<string, unknown> }> };
  };
  for (const schema of Object.values(spec.components.schemas)) {
    const [first] = Object.keys(schema.properties ?? {});
    if (schema.properties && first !== undefined) {
      schema.properties[`${first}_drifted`] = schema.properties[first];
      delete schema.properties[first];
      break;
    }
  }
  writeFileSync(path, JSON.stringify(spec, null, 2) + '\n');
}

/** The script's own temp dirs left in the private temp dir (uv drops its own lock files there). */
function driftDirs(): string[] {
  return readdirSync(priv).filter((n) => n.startsWith('client-drift-'));
}

function snapshot(): { bytes: Buffer; mtime: number }[] {
  return [realSpec, realSchema].map((p) => ({
    bytes: readFileSync(p),
    mtime: statSync(p).mtimeMs,
  }));
}

beforeAll(() => {
  dir = mkdtempSync(join(tmpdir(), 'drift-acc-'));
  priv = mkdtempSync(join(tmpdir(), 'drift-priv-'));
});
afterAll(() => {
  rmSync(dir, { recursive: true, force: true });
  rmSync(priv, { recursive: true, force: true });
});

describe('AC2: freshly regenerated client (real exporter)', () => {
  it(
    'exits 0 against the committed files and prints only the up-to-date line',
    () => {
      const r = run([]);
      expect(r.code).toBe(0);
      expect(r.out.trim()).toBe('API client is up to date.');
      expect(r.err).toBe('');
    },
    TIMEOUT,
  );
});

describe('AC1: stale client (real exporter)', () => {
  it(
    'exits 1 listing only the spec when the committed spec is stale',
    () => {
      const spec = join(dir, 'stale-openapi.json');
      staleSpec(spec);
      const r = run(['--spec', spec, '--schema', realSchema]);
      expect(r.code).toBe(1);
      const lines = r.err.split('\n').map((l) => l.trim());
      expect(lines.filter((l) => l === slashed(spec))).toHaveLength(1);
      expect(r.err).not.toContain('schema.d.ts');
      expect(r.err).toContain(HINT);
      expect(r.out).toBe('');
    },
    TIMEOUT,
  );

  it(
    'lists both files, each once, when the schema was regenerated from the stale spec',
    () => {
      const spec = join(dir, 'both-openapi.json');
      const schema = join(dir, 'both-schema.d.ts');
      staleSpec(spec);
      const g = spawnSync(process.execPath, [generate, '--input', spec, '--output', schema], {
        encoding: 'utf8',
      });
      expect(g.status).toBe(0);
      const r = run(['--spec', spec, '--schema', schema]);
      expect(r.code).toBe(1);
      const lines = r.err.split('\n').map((l) => l.trim());
      expect(lines.filter((l) => l === slashed(spec))).toHaveLength(1);
      expect(lines.filter((l) => l === slashed(schema))).toHaveLength(1);
      expect(r.err).toContain(HINT);
    },
    TIMEOUT,
  );

  it(
    'prints repo-relative forward-slash paths for the default committed files',
    () => {
      const exporter = writeExporter('other-export.mjs', otherSpecBody);
      const r = run(['--export-cmd', JSON.stringify([process.execPath, exporter])]);
      expect(r.code).toBe(1);
      expect(r.err).toContain('  packages/api-client/openapi.json');
      expect(r.err).toContain('  packages/api-client/src/generated/schema.d.ts');
      expect(r.err).not.toContain('\\');
    },
    TIMEOUT,
  );
});

describe('side effects', () => {
  it(
    'leaves committed bytes and mtimes alone and removes its temp dir on a drift run',
    () => {
      const exporter = writeExporter('other-export2.mjs', otherSpecBody);
      const before = snapshot();
      const r = run(['--export-cmd', JSON.stringify([process.execPath, exporter])]);
      expect(r.code).toBe(1);
      const after = snapshot();
      expect(after.map((f) => f.bytes.toString('base64'))).toEqual(
        before.map((f) => f.bytes.toString('base64')),
      );
      expect(after.map((f) => f.mtime)).toEqual(before.map((f) => f.mtime));
      expect(driftDirs()).toEqual([]);
    },
    TIMEOUT,
  );

  it(
    'removes its temp dir after a failed export',
    () => {
      const r = run(['--export-cmd', JSON.stringify([process.execPath, exitScript(3)])]);
      expect(r.code).toBe(2);
      expect(driftDirs()).toEqual([]);
    },
    TIMEOUT,
  );
});

describe('exit 2 paths', () => {
  it(
    'exits 2 with a "client:check error:" message when the generator rejects the spec',
    () => {
      const exporter = writeExporter('garbage-export.mjs', 'this is not an openapi document');
      const r = run(['--export-cmd', JSON.stringify([process.execPath, exporter])]);
      expect(r.code).toBe(2);
      expect(r.err.startsWith('client:check error:')).toBe(true);
      expect(r.err).toContain('Client generation failed');
    },
    TIMEOUT,
  );

  it(
    'treats a missing --spec file as drift and lists it',
    () => {
      const missing = join(dir, 'does-not-exist.json');
      expect(existsSync(missing)).toBe(false);
      const exporter = writeExporter('real-copy-export.mjs', readFileSync(realSpec, 'utf8'));
      const r = run([
        '--spec',
        missing,
        '--export-cmd',
        JSON.stringify([process.execPath, exporter]),
      ]);
      expect(r.code).toBe(1);
      expect(r.err).toContain(slashed(missing));
    },
    TIMEOUT,
  );

  it('exits 2 when --export-cmd is not a JSON array', () => {
    const r = run(['--export-cmd', 'not json']);
    expect(r.code).toBe(2);
    expect(r.err.startsWith('client:check error:')).toBe(true);
  });

  it('exits 2 when a flag value looks like another flag', () => {
    const r = run(['--spec', '--schema', 'x']);
    expect(r.code).toBe(2);
    expect(r.err.startsWith('client:check error:')).toBe(true);
    expect(r.err).toContain('--spec needs a value');
  });

  it(
    'exits 2 with a failure message when a step exceeds the timeout',
    () => {
      const sleeper = join(dir, 'sleeper.mjs');
      writeFileSync(sleeper, 'setTimeout(() => {}, 30000);\n');
      const r = run(['--export-cmd', JSON.stringify([process.execPath, sleeper])], {
        CLIENT_CHECK_TIMEOUT_MS: '500',
      });
      expect(r.code).toBe(2);
      expect(r.err).toContain('OpenAPI export failed:');
      expect(r.err).toContain('ETIMEDOUT');
      expect(driftDirs()).toEqual([]);
    },
    TIMEOUT,
  );

  it('exits 2 when a flag has no value', () => {
    const r = run(['--spec']);
    expect(r.code).toBe(2);
    expect(r.err.startsWith('client:check error:')).toBe(true);
  });
});

describe('flag and env precedence', () => {
  it(
    'reads CLIENT_CHECK_EXPORT_CMD from the environment',
    () => {
      const r = run([], {
        CLIENT_CHECK_EXPORT_CMD: JSON.stringify([process.execPath, exitScript(5)]),
      });
      expect(r.code).toBe(2);
      expect(r.err).toContain('OpenAPI export failed (exit 5)');
    },
    TIMEOUT,
  );

  it(
    'lets the flag win over the env var',
    () => {
      const r = run(['--export-cmd', JSON.stringify([process.execPath, exitScript(6)])], {
        CLIENT_CHECK_EXPORT_CMD: JSON.stringify([process.execPath, exitScript(5)]),
      });
      expect(r.code).toBe(2);
      expect(r.err).toContain('exit 6');
      expect(r.err).not.toContain('exit 5');
    },
    TIMEOUT,
  );

  it(
    'lets --spec win over CLIENT_CHECK_SPEC',
    () => {
      const envSpec = join(dir, 'env-spec.json');
      const flagSpec = join(dir, 'flag-spec.json');
      const exporter = writeExporter('copy-real-export.mjs', readFileSync(realSpec, 'utf8'));
      copyFileSync(realSpec, flagSpec);
      writeFileSync(envSpec, '{}');
      const r = run(
        [
          '--spec',
          flagSpec,
          '--schema',
          realSchema,
          '--export-cmd',
          JSON.stringify([process.execPath, exporter]),
        ],
        { CLIENT_CHECK_SPEC: envSpec },
      );
      expect(r.code).toBe(0);
    },
    TIMEOUT,
  );

  it(
    'reads CLIENT_CHECK_SPEC when no --spec flag is given',
    () => {
      const envSpec = join(dir, 'env-only-spec.json');
      writeFileSync(envSpec, '{}');
      const exporter = writeExporter('copy-real-export2.mjs', readFileSync(realSpec, 'utf8'));
      const r = run(['--export-cmd', JSON.stringify([process.execPath, exporter])], {
        CLIENT_CHECK_SPEC: envSpec,
      });
      expect(r.code).toBe(1);
      expect(r.err).toContain(slashed(envSpec));
    },
    TIMEOUT,
  );
});

describe('package.json scripts', () => {
  const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8')) as {
    scripts: Record<string, string>;
  };

  it('defines client:check as the drift script', () => {
    expect(pkg.scripts['client:check']).toBe('node scripts/client-drift-check.js');
  });

  it('defines client:generate as export then generate', () => {
    expect(pkg.scripts['client:generate']).toBe(
      'pnpm openapi:export && pnpm --filter @mtg-stats/api-client generate',
    );
  });
});
