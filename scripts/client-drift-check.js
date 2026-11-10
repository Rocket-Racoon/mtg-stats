// Client drift check (S-1.5.5): fails when the committed OpenAPI spec or generated TypeScript
// schema no longer matches what the API code produces. Never writes to the working tree.
//
//   node scripts/client-drift-check.js [--spec <openapi.json>] [--schema <schema.d.ts>]
//                                      [--export-cmd <json array>]
//
// Steps: export the spec from the API into a temp dir, generate schema.d.ts from that spec into
// the temp dir, then compare both with the committed files (LF-normalised, as the generator does).
//
// Flags (each also readable from an env var; the flag wins):
//   --spec <path>        committed spec (CLIENT_CHECK_SPEC),
//                        default packages/api-client/openapi.json
//   --schema <path>      committed generated types (CLIENT_CHECK_SCHEMA),
//                        default packages/api-client/src/generated/schema.d.ts
//   --export-cmd <json>  JSON array holding the export command; `--output <tmp>` is appended
//                        (CLIENT_CHECK_EXPORT_CMD), default
//                        ["uv","run","--directory","apps/api","python","-m","app.scripts.export_openapi"]
//
// Exit codes: 0 up to date; 1 drift (changed files are listed); 2 the export or generation failed.
import { spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, relative, resolve, sep } from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const EXIT_DRIFT = 1;
const EXIT_ERROR = 2;
const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const clientDir = join(repoRoot, 'packages', 'api-client');
const HINT = 'Run `pnpm client:generate` and commit the result.';
const DEFAULT_EXPORT_CMD = [
  'uv',
  'run',
  '--directory',
  'apps/api',
  'python',
  '-m',
  'app.scripts.export_openapi',
];

class CheckError extends Error {}

function parseExportCmd(text) {
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    parsed = null;
  }
  if (
    !Array.isArray(parsed) ||
    parsed.length === 0 ||
    !parsed.every((s) => typeof s === 'string')
  ) {
    throw new CheckError('--export-cmd must be a non-empty JSON array of strings');
  }
  return parsed;
}

function parseArgs(argv, env) {
  const opts = {
    spec: env.CLIENT_CHECK_SPEC,
    schema: env.CLIENT_CHECK_SCHEMA,
    exportCmd: env.CLIENT_CHECK_EXPORT_CMD,
  };
  const flags = { '--spec': 'spec', '--schema': 'schema', '--export-cmd': 'exportCmd' };
  for (let i = 0; i < argv.length; i++) {
    const key = flags[argv[i]];
    if (!key) throw new CheckError(`Unknown argument: ${argv[i]}`);
    const value = argv[++i];
    if (!value) throw new CheckError(`${argv[i - 1]} needs a value`);
    opts[key] = value;
  }
  return {
    spec: resolve(process.cwd(), opts.spec ?? join(clientDir, 'openapi.json')),
    schema: resolve(
      process.cwd(),
      opts.schema ?? join(clientDir, 'src', 'generated', 'schema.d.ts'),
    ),
    exportCmd: opts.exportCmd ? parseExportCmd(opts.exportCmd) : DEFAULT_EXPORT_CMD,
  };
}

function run(label, command, args, cwd) {
  const result = spawnSync(command, args, { cwd, encoding: 'utf8' });
  if (result.error) throw new CheckError(`${label} failed to start: ${result.error.message}`);
  if (result.status !== 0) {
    const output = `${result.stdout ?? ''}${result.stderr ?? ''}`.trim();
    throw new CheckError(`${label} failed (exit ${result.status}).\n${output}`);
  }
}

function readNormalised(path) {
  return existsSync(path) ? readFileSync(path, 'utf8').replace(/\r\n/g, '\n') : null;
}

function display(path) {
  const rel = relative(repoRoot, path);
  return (rel.startsWith('..') ? path : rel).split(sep).join('/');
}

function main() {
  const { spec, schema, exportCmd } = parseArgs(process.argv.slice(2), process.env);
  const tmp = mkdtempSync(join(tmpdir(), 'client-drift-'));
  try {
    const freshSpec = join(tmp, 'openapi.json');
    const freshSchema = join(tmp, 'schema.d.ts');
    const [command, ...base] = exportCmd;
    run('OpenAPI export', command, [...base, '--output', freshSpec], repoRoot);
    run(
      'Client generation',
      process.execPath,
      [join(clientDir, 'scripts', 'generate.mjs'), '--input', freshSpec, '--output', freshSchema],
      clientDir,
    );
    const drifted = [
      [spec, freshSpec],
      [schema, freshSchema],
    ]
      .filter(([committed, fresh]) => readNormalised(committed) !== readNormalised(fresh))
      .map(([committed]) => display(committed));
    if (drifted.length === 0) {
      console.log('API client is up to date.');
      return 0;
    }
    console.error('API client drift detected. Changed files:');
    for (const file of drifted) console.error(`  ${file}`);
    console.error(HINT);
    return EXIT_DRIFT;
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
}

try {
  process.exitCode = main();
} catch (error) {
  console.error(`client:check error: ${error instanceof Error ? error.message : String(error)}`);
  process.exitCode = EXIT_ERROR;
}
