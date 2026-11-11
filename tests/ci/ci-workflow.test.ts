/**
 * S-1.8.2 / S-1.8.3 repo-side guards for .github/workflows/ci.yml. No YAML library is available at
 * the root, so the workflow is read with a small indentation-aware splitter: top-level keys, a
 * named job's keys and its steps (an absent job reads as empty, so tests fail closed). Assertions
 * are about behavior (what runs, in which order, with which images), not exact formatting.
 */
import { spawnSync } from 'node:child_process';
import { chmodSync, existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

const root = join(import.meta.dirname, '..', '..');
const read = (p: string) => readFileSync(join(root, p), 'utf8').replace(/\r\n/g, '\n');
const workflow = read('.github/workflows/ci.yml');

const indentOf = (line: string) => line.length - line.trimStart().length;
const isContent = (line: string) => line.trim() !== '' && !line.trim().startsWith('#');

/** Lines of the block that follows `key:` at `indent`, until the next line at that indent or less. */
function block(text: string, key: string, indent: number): string[] {
  const lines = text.split('\n');
  const start = lines.findIndex(
    (l) => indentOf(l) === indent && l.trim().replace(/:.*$/, '') === key && isContent(l),
  );
  if (start === -1) return [];
  const out: string[] = [];
  for (const l of lines.slice(start + 1)) {
    if (isContent(l) && indentOf(l) <= indent) break;
    out.push(l);
  }
  return out;
}

const topLevel = (key: string) => block(workflow, key, 0).join('\n');
const jobsText = topLevel('jobs');
/** The text of the job with this id (a key at indent 2 under `jobs:`); empty when absent. */
const jobText = (id: string) => block(jobsText, id, 2).join('\n');
const apiJob = jobText('api');
const tsJob = jobText('ts');
const jobBlock = (key: string) => block(apiJob, key, 4).join('\n');

interface Step {
  text: string;
  id: string;
  name: string;
  uses: string;
  run: string;
  condition: string;
  env: string;
  with: string;
}

/** Splits a job's `steps:` list on its `- ` item markers (list items sit at indent 6). */
function parseSteps(job: string): Step[] {
  const lines = block(job, 'steps', 4);
  const items: string[][] = [];
  for (const l of lines) {
    if (/^\s*- /.test(l) && indentOf(l) === 6) items.push([l.replace('- ', '  ')]);
    else items.at(-1)?.push(l);
  }
  return items.map((it) => {
    const text = it.join('\n');
    const field = (k: string) => new RegExp(`^ {8}${k}:[ \\t]*(.*)$`, 'm').exec(text)?.[1] ?? '';
    const runIdx = it.findIndex((l) => /^ {8}run:/.test(l));
    let run = '';
    if (runIdx !== -1) {
      run = (it[runIdx] as string)
        .replace(/^ {8}run:\s*[>|][-+]?\s*/, '')
        .replace(/^ {8}run:\s*/, '');
      for (const l of it.slice(runIdx + 1)) {
        if (isContent(l) && indentOf(l) <= 8) break;
        run += `\n${l.trim()}`;
      }
    }
    return {
      text,
      id: field('id'),
      name: field('name'),
      uses: field('uses'),
      run,
      condition: field('if'),
      env: block(text, 'env', 8).join('\n'),
      with: block(text, 'with', 8).join('\n'),
    };
  });
}
const steps = parseSteps(apiJob);
const tsSteps = parseSteps(tsJob);
const stepIndex = (pred: (s: Step) => boolean) => steps.findIndex(pred);
const verifyIdx = stepIndex((s) => /node scripts\/verify\.js --only api\b/.test(s.run));

const gitBash = ['C:/Program Files/Git/bin/bash.exe', 'C:/Program Files/Git/usr/bin/bash.exe'];
/** bash for executing workflow scripts like the runner does; undefined (tests skip) when absent. */
const bash =
  process.platform === 'win32'
    ? gitBash.find((p) => existsSync(p))
    : spawnSync('bash', ['-c', 'exit 0']).status === 0
      ? 'bash'
      : undefined;

function verifyList(half: 'api' | 'ts' = 'api'): string {
  const r = spawnSync(
    process.execPath,
    [join(root, 'scripts', 'verify.js'), '--list', '--only', half],
    { encoding: 'utf8', cwd: root },
  );
  expect(r.status).toBe(0);
  return r.stdout;
}

describe('triggers and permissions', () => {
  it('runs on pull requests and on pushes to main only', () => {
    const on = topLevel('on');
    expect(on).toMatch(/^ {2}pull_request:/m);
    expect(on).toMatch(/^ {2}push:/m);
    const push = block(on, 'push', 2).join('\n');
    expect(push).toMatch(/branches:\s*(\[\s*main\s*\]|\n\s*- main\b)/);
  });

  it('grants the workflow read-only contents permission', () => {
    const perms = topLevel('permissions').split('\n').filter(isContent);
    expect(perms.map((l) => l.trim())).toEqual(['contents: read']);
  });
});

describe('the api job', () => {
  it('exists with id and name "api" on an Ubuntu runner', () => {
    expect(apiJob).not.toBe('');
    expect(apiJob).toMatch(/^ {4}name:\s*api\s*$/m);
    expect(apiJob).toMatch(/^ {4}runs-on:\s*ubuntu-/m);
  });

  it('uses Postgres 16 and Redis 7 service containers', () => {
    const services = jobBlock('services');
    expect(block(services, 'postgres', 6).join('\n')).toMatch(/image:\s*postgres:16[.\w-]*\s*$/m);
    expect(block(services, 'redis', 6).join('\n')).toMatch(/image:\s*redis:7[.\w-]*\s*$/m);
  });

  it('uses exactly the image tags of infra/docker-compose.yml', () => {
    const compose = block(read('infra/docker-compose.yml'), 'services', 0).join('\n');
    const image = (text: string, svc: string, indent: number) =>
      /image:\s*(\S+)/.exec(block(text, svc, indent).join('\n'))?.[1];
    const services = jobBlock('services');
    expect(image(compose, 'postgres', 2)).toMatch(/^postgres:16/);
    expect(image(services, 'postgres', 6)).toBe(image(compose, 'postgres', 2));
    expect(image(compose, 'redis', 2)).toMatch(/^redis:7/);
    expect(image(services, 'redis', 6)).toBe(image(compose, 'redis', 2));
  });

  it('sets APP_ENV, DATABASE_URL (database mtg_stats) and REDIS_URL for the job', () => {
    const env = jobBlock('env');
    expect(env).toMatch(/^ {6}APP_ENV:\s*\S+/m);
    expect(env).toMatch(/^ {6}DATABASE_URL:\s*postgresql\+psycopg:\/\/\S+\/mtg_stats\s*$/m);
    expect(env).toMatch(/^ {6}REDIS_URL:\s*redis:\/\/\S+/m);
  });

  it('gives both services a health check so steps wait for them', () => {
    const services = jobBlock('services');
    expect(block(services, 'postgres', 6).join('\n')).toContain('--health-cmd');
    expect(block(services, 'redis', 6).join('\n')).toContain('--health-cmd');
  });
});

describe('AC1: the checks that run', () => {
  it('runs `node scripts/verify.js --only api`', () => {
    expect(verifyIdx).toBeGreaterThanOrEqual(0);
  });

  it('covers all six API steps of verify, with the real tools', () => {
    const out = verifyList();
    const names = [...out.matchAll(/^\[\d+\/\d+\] (.+)$/gm)].map((m) => m[1]);
    expect(names).toEqual([
      'secrets check',
      'ruff check',
      'ruff format --check',
      'mypy --strict',
      'pytest',
      'alembic check',
    ]);
    expect(out).toContain('secrets_check.py');
    expect(out).toContain('ruff check');
    expect(out).toContain('ruff format --check');
    expect(out).toContain('mypy --strict');
    expect(out).toContain('--cov=app');
    expect(out).toContain('alembic check');
  });

  it('does not use a sparse checkout, so the secrets check sees every file', () => {
    const checkout = steps.find((s) => s.uses.startsWith('actions/checkout@'));
    expect(checkout).toBeDefined();
    expect(checkout?.text).not.toMatch(/sparse-checkout/);
  });

  it('migrates the CI database to head before the verify step', () => {
    const up = stepIndex((s) => /alembic upgrade head/.test(s.run));
    expect(up).toBeGreaterThanOrEqual(0);
    expect(up).toBeLessThan(verifyIdx);
  });

  it('creates the test database from the Compose init SQL before verify', () => {
    expect(existsSync(join(root, 'infra/postgres/init/01-create-test-db.sql'))).toBe(true);
    const create = stepIndex((s) => s.run.includes('01-create-test-db.sql'));
    expect(create).toBeGreaterThanOrEqual(0);
    expect(create).toBeLessThan(verifyIdx);
  });

  it('runs an Alembic round-trip after verify: downgrade base, upgrade head, then check', () => {
    const idx = stepIndex((s) => /alembic downgrade base/.test(s.run));
    expect(idx).toBeGreaterThan(verifyIdx);
    const run = steps[idx]?.run ?? '';
    const down = run.indexOf('alembic downgrade base');
    const up = run.indexOf('alembic upgrade head', down);
    const check = run.indexOf('alembic check', up);
    expect(down).toBeGreaterThanOrEqual(0);
    expect(up).toBeGreaterThan(down);
    expect(check).toBeGreaterThan(up);
  });
});

describe('AC2: coverage threshold and summary', () => {
  const summaryIdx = stepIndex(
    (s) => s.run.includes('GITHUB_STEP_SUMMARY') && /overage/.test(s.run),
  );

  it('has a coverage summary step that writes to the job summary', () => {
    expect(summaryIdx).toBeGreaterThanOrEqual(0);
    expect(steps[summaryIdx]?.run).toMatch(/(>>|tee -a)\s*"?\$GITHUB_STEP_SUMMARY"?/);
  });

  it('runs the summary even when an earlier step failed, after verify', () => {
    expect(steps[summaryIdx]?.condition).toMatch(/!cancelled\(\)|always\(\)/);
    expect(summaryIdx).toBeGreaterThan(verifyIdx);
  });

  it('states the same minimum as the --cov-fail-under in the verify pytest step', () => {
    const fail = /--cov-fail-under=(\d+)/.exec(verifyList())?.[1];
    expect(fail).toBe('85');
    const run = steps[summaryIdx]?.run ?? '';
    const mentioned = [...run.matchAll(/(?:>=|minimum)\s*(\d+)/g)].map((m) => m[1]);
    expect(mentioned.length).toBeGreaterThan(0);
    expect(new Set(mentioned)).toEqual(new Set([fail]));
  });

  it('does not lower or bypass the threshold in the workflow', () => {
    const code = workflow.replace(/^\s*#.*$/gm, '').replace(/\s#\s.*$/gm, '');
    expect(code).not.toMatch(/--cov-fail-under/);
    expect(code).not.toMatch(/--no-cov\b/);
    expect(code).not.toMatch(/-p\s*=?\s*no:(pytest_)?cov\b/);
  });
});

describe('AC2: the coverage summary script, executed', () => {
  const script = steps.find((s) => s.name === 'Coverage summary')?.run ?? '';

  let dir: string;
  let summary: string;
  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), 'cov-summary-'));
    summary = join(dir, 'summary.md');
    writeFileSync(summary, '');
    // Stub `uv run [--frozen] coverage report --format=total [--precision=2]`.
    const stub = [
      '#!/bin/sh',
      '[ "$STUB_UV_FAIL" = 1 ] && exit 1',
      'case "$*" in',
      '  *--precision=2*) echo "$STUB_SHOWN" ;;',
      '  *--format=total*) echo "$STUB_GATE" ;;',
      '  *) exit 2 ;;',
      'esac',
      '',
    ].join('\n');
    writeFileSync(join(dir, 'uv'), stub, { mode: 0o755 });
    chmodSync(join(dir, 'uv'), 0o755);
  });
  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  function exec(opts: { data: boolean; shown?: string; gate?: string; fail?: boolean }) {
    if (opts.data) writeFileSync(join(dir, '.coverage'), '');
    const sep = process.platform === 'win32' ? ';' : ':';
    const r = spawnSync(bash as string, ['-e', '-c', script], {
      cwd: dir,
      encoding: 'utf8',
      env: {
        ...process.env,
        PATH: `${dir}${sep}${process.env['PATH'] ?? ''}`,
        GITHUB_STEP_SUMMARY: summary,
        STUB_SHOWN: opts.shown ?? '',
        STUB_GATE: opts.gate ?? '',
        STUB_UV_FAIL: opts.fail ? '1' : '0',
      },
    });
    return { code: r.status, md: readFileSync(summary, 'utf8'), err: r.stderr };
  }

  it.skipIf(!bash)('says coverage was not measured when there is no .coverage file', () => {
    const r = exec({ data: false });
    expect(r.code).toBe(0);
    expect(r.md).toContain('not measured');
  });

  it.skipIf(!bash)('shows 94.50% as passed against the 85% minimum', () => {
    const r = exec({ data: true, shown: '94.50', gate: '95' });
    expect(r.code).toBe(0);
    expect(r.md).toContain('94.50% (minimum 85%, passed)');
  });

  it.skipIf(!bash)('judges 84.60 by the gate rounding (85) and reports passed', () => {
    const r = exec({ data: true, shown: '84.60', gate: '85' });
    expect(r.code).toBe(0);
    expect(r.md).toContain('84.60% (minimum 85%, passed)');
  });

  it.skipIf(!bash)('flags 80.12% as below the minimum', () => {
    const r = exec({ data: true, shown: '80.12', gate: '80' });
    expect(r.code).toBe(0);
    expect(r.md).toContain('80.12% (minimum 85%, **below the minimum**)');
  });

  it.skipIf(!bash)('reports "could not be read" and exits 0 when coverage fails', () => {
    const r = exec({ data: true, fail: true });
    expect(r.code).toBe(0);
    expect(r.md).toContain('could not be read');
  });

  it('found the Coverage summary step script to execute', () => {
    expect(script).toContain('GITHUB_STEP_SUMMARY');
  });
});

describe('AC4: coverage report artifact, nothing committed', () => {
  const upload = steps.find((s) => s.uses.startsWith('actions/upload-artifact@'));

  it('uploads the coverage report with 7 days retention', () => {
    expect(upload).toBeDefined();
    expect(upload?.text).toMatch(/^ {10}retention-days:\s*7\s*$/m);
    expect(upload?.text).toMatch(/coverage\.xml|htmlcov/);
  });

  it('uploads even when an earlier step failed', () => {
    expect(upload?.condition).toMatch(/!cancelled\(\)|always\(\)/);
  });

  it('ignores coverage files in .gitignore', () => {
    const ignored = read('.gitignore')
      .split('\n')
      .map((l) => l.trim());
    expect(ignored).toContain('.coverage');
    expect(ignored).toContain('coverage.xml');
    expect(ignored).toContain('htmlcov/');
  });

  it('has no coverage file tracked by git', () => {
    const r = spawnSync('git', ['ls-files'], { encoding: 'utf8', cwd: root, maxBuffer: 64e6 });
    expect(r.status).toBe(0);
    const bad = r.stdout
      .split('\n')
      .filter((f) => /(^|\/)(\.coverage(\..*)?|coverage\.xml|htmlcov\/.*)$/.test(f));
    expect(bad).toEqual([]);
  });
});

describe('AC3: uv cache', () => {
  it('enables the setup-uv cache keyed on apps/api/uv.lock', () => {
    const uv = steps.find((s) => s.uses.startsWith('astral-sh/setup-uv@'));
    expect(uv).toBeDefined();
    expect(uv?.text).toMatch(/enable-cache:\s*true/);
    expect(uv?.text).toMatch(/cache-dependency-glob:\s*['"]?apps\/api\/uv\.lock['"]?\s*$/m);
    expect(existsSync(join(root, 'apps/api/uv.lock'))).toBe(true);
  });

  it('installs from the lock file without changing it', () => {
    expect(steps.some((s) => /uv sync\b.*--(locked|frozen)/.test(s.run))).toBe(true);
  });

  it('has a job timeout of at most 15 minutes so a hang cannot burn the budget', () => {
    const t = /^ {4}timeout-minutes:\s*(\d+)/m.exec(apiJob)?.[1];
    expect(t).toBeDefined();
    expect(Number(t)).toBeLessThanOrEqual(15);
  });
});

describe('supply chain', () => {
  it('pins every action to a 40-hex commit SHA with a version comment', () => {
    const uses = [...workflow.matchAll(/^\s*(?:-\s*)?uses:\s*(.+)$/gm)].map((m) => m[1] as string);
    expect(uses.length).toBeGreaterThanOrEqual(4);
    for (const u of uses) {
      expect(u).toMatch(/^[\w.-]+\/[\w./-]+@[0-9a-f]{40}\s+# v\d/);
    }
  });

  it.each([
    ['api', steps],
    ['ts', tsSteps],
  ])('pins every action of the %s job (and the job has some)', (_job, jobSteps) => {
    const used = jobSteps.filter((s) => s.uses !== '');
    expect(used.length).toBeGreaterThanOrEqual(3);
    for (const s of used) {
      expect(s.uses).toMatch(/^[\w.-]+\/[\w./-]+@[0-9a-f]{40}\s+# v\d/);
    }
  });

  it.each([
    ['api', steps],
    ['ts', tsSteps],
  ])('checks out with persist-credentials: false in the %s job', (_job, jobSteps) => {
    const checkouts = jobSteps.filter((s) => s.uses.startsWith('actions/checkout@'));
    expect(checkouts.length).toBeGreaterThanOrEqual(1);
    for (const c of checkouts) {
      expect(c.with).toMatch(/^\s*persist-credentials:\s*false\s*$/m);
    }
  });
});

describe('S-1.8.3 AC1: the ts job', () => {
  const idx = (pred: (s: Step) => boolean) => tsSteps.findIndex(pred);
  const uvSync = idx((s) => /\buv sync\b.*--locked/.test(s.run));
  const install = idx((s) => /^pnpm install --frozen-lockfile\s*$/.test(s.run.trim()));
  const verify = idx((s) => /^pnpm verify:ts\s*$/.test(s.run.trim()));
  const build = idx((s) =>
    /^pnpm --filter @mtg-stats\/web --fail-if-no-match build\s*$/.test(s.run.trim()),
  );

  it('exists with id and name "ts" on an Ubuntu runner, as its own job', () => {
    expect(tsJob).not.toBe('');
    expect(tsJob).toMatch(/^ {4}name:\s*ts\s*$/m);
    expect(tsJob).toMatch(/^ {4}runs-on:\s*ubuntu-/m);
    expect(tsJob).not.toBe(apiJob);
  });

  it('is a separate job from api: the api job has no ts steps and vice versa', () => {
    expect(apiJob).not.toMatch(/verify:ts|--frozen-lockfile|pnpm /);
    expect(tsJob).not.toMatch(/alembic|services:|verify\.js --only api/);
  });

  it('runs in parallel with api: neither job declares needs', () => {
    expect(tsJob).not.toMatch(/^ {4}needs:/m);
    expect(apiJob).not.toMatch(/^ {4}needs:/m);
  });

  it('runs on pull requests and pushes to main like the api job (workflow-level triggers)', () => {
    expect(topLevel('on')).toMatch(/^ {2}pull_request:/m);
    expect(topLevel('on')).toMatch(/^ {2}push:/m);
  });

  it('has all its steps: uv sync, pnpm install, verify:ts, then the web build', () => {
    expect(uvSync).toBeGreaterThanOrEqual(0);
    expect(install).toBeGreaterThanOrEqual(0);
    expect(verify).toBeGreaterThanOrEqual(0);
    expect(build).toBeGreaterThanOrEqual(0);
  });

  it('orders them: uv sync, then pnpm install, then verify, then build', () => {
    expect(uvSync).toBeLessThan(install);
    expect(install).toBeLessThan(verify);
    expect(verify).toBeLessThan(build);
  });

  it('runs uv sync in apps/api (where the lock file lives)', () => {
    const s = tsSteps[uvSync];
    expect(s?.text).toMatch(/^ {8}working-directory:\s*apps\/api\s*$/m);
  });

  it('sets VITE_API_BASE_URL (an absolute URL) for the web build', () => {
    expect(tsSteps[build]?.env).toMatch(/^\s*VITE_API_BASE_URL:\s*https?:\/\/\S+/m);
  });

  it('bakes the verify half into the TS checks: exactly the six tools of AC1', () => {
    const out = verifyList('ts');
    const names = [...out.matchAll(/^\[\d+\/\d+\] (.+)$/gm)].map((m) => m[1]);
    expect(names).toEqual([
      'client drift check',
      'eslint',
      'prettier --check',
      'tsc --noEmit',
      'vitest',
      'jest',
    ]);
    expect(out).toContain('pnpm client:check');
    expect(out).toContain('pnpm lint');
    expect(out).toContain('pnpm format:check');
    expect(out).toContain('pnpm typecheck');
    expect(out).toMatch(/vitest run/);
    expect(out).toMatch(/@mtg-stats\/mobile .*test/);
  });

  it('gets pnpm from pnpm/action-setup without a hard-coded version that could drift', () => {
    const pnpm = tsSteps.find((s) => s.uses.startsWith('pnpm/action-setup@'));
    expect(pnpm).toBeDefined();
    const declared = /^\s*version:\s*['"]?([^\s'"]+)/m.exec(pnpm?.with ?? '')?.[1];
    const pm = /"packageManager":\s*"pnpm@([^"]+)"/.exec(read('package.json'))?.[1];
    expect(pm).toBeDefined();
    if (declared !== undefined) expect(declared).toBe(pm);
  });
});

describe('S-1.8.3 AC2: failed step named in the summary', () => {
  const summaryIdx = tsSteps.findIndex((s) => s.name === 'Job summary');
  const summary = tsSteps[summaryIdx];
  const tracked = tsSteps.slice(0, Math.max(summaryIdx, 0)).filter((s) => s.id !== '');
  const byName = (n: string) => tsSteps.find((s) => s.name === n);

  // env var name -> step id it reports, read from `NAME: ${{ steps.<id>.outcome }}`.
  const envMap = new Map<string, string>(
    [
      ...(summary?.env ?? '').matchAll(
        /^\s*(\w+):\s*\$\{\{\s*steps\.([\w-]+)\.outcome\s*\}\}\s*$/gm,
      ),
    ].map((m) => [m[1] as string, m[2] as string]),
  );

  it('has a "Job summary" step, last, that runs even after a failure', () => {
    expect(summary).toBeDefined();
    expect(summaryIdx).toBe(tsSteps.length - 1);
    expect(summary?.condition).toMatch(/!cancelled\(\)|always\(\)/);
  });

  it('writes to the job summary file', () => {
    expect(summary?.run).toMatch(/(>>|tee -a)\s*"?\$GITHUB_STEP_SUMMARY"?/);
  });

  it('tracks the install, verify and build steps, each with an id', () => {
    for (const name of [
      'Install TypeScript dependencies',
      'Verify (TypeScript half)',
      'Web production build',
    ]) {
      expect(byName(name)?.id, name).toMatch(/^[\w-]+$/);
    }
  });

  it('receives every earlier step that has an id through env, one variable each', () => {
    expect(tracked.length).toBeGreaterThanOrEqual(3);
    const reported = [...envMap.values()].sort();
    expect(reported).toEqual(tracked.map((s) => s.id).sort());
  });

  it('every step id is unique within the job', () => {
    const ids = tsSteps.map((s) => s.id).filter((i) => i !== '');
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('never interpolates ${{ }} expressions inside the summary script (injection hygiene)', () => {
    expect(summary?.run).not.toBe('');
    expect(summary?.run).not.toContain('${{');
  });

  it('reports every tracked step by its name in the script or its row labels', () => {
    for (const s of tracked) expect(summary?.run, s.name).toContain(s.name);
  });

  it('runs the verify step as `pnpm verify:ts`, which writes its own step table', () => {
    expect(byName('Verify (TypeScript half)')?.run.trim()).toBe('pnpm verify:ts');
  });

  describe('the script, executed', () => {
    let dir: string;
    let out: string;
    beforeEach(() => {
      dir = mkdtempSync(join(tmpdir(), 'ts-summary-'));
      out = join(dir, 'summary.md');
      writeFileSync(out, '');
    });
    afterEach(() => {
      rmSync(dir, { recursive: true, force: true });
    });

    /**
     * outcomes: step name -> outcome; unlisted tracked steps are "success". jobStatus defaults to
     * what the runner would report: the worst tracked outcome (failure, cancelled) or success.
     */
    function exec(outcomes: Record<string, string>, jobStatus?: string) {
      const values = Object.values(outcomes);
      const status =
        jobStatus ??
        (values.includes('failure')
          ? 'failure'
          : values.includes('cancelled')
            ? 'cancelled'
            : 'success');
      const env: Record<string, string> = { JOB_STATUS: status };
      for (const [name, id] of envMap) {
        const step = tracked.find((s) => s.id === id);
        env[name] = outcomes[step?.name ?? ''] ?? 'success';
      }
      const r = spawnSync(bash as string, ['-e', '-c', summary?.run ?? ''], {
        cwd: dir,
        encoding: 'utf8',
        env: { ...process.env, ...env, GITHUB_STEP_SUMMARY: out },
      });
      const md = readFileSync(out, 'utf8');
      const row = (name: string) =>
        md.split('\n').find((l) => l.startsWith('|') && l.includes(name)) ?? '';
      return { code: r.status, md, row, err: r.stderr };
    }

    it('found the Job summary script and its variables to execute', () => {
      expect(summary?.run).toContain('GITHUB_STEP_SUMMARY');
      expect(envMap.size).toBe(tracked.length);
    });

    it.skipIf(!bash)('shows no FAILED heading and every row "passed" when all succeed', () => {
      const r = exec({}, 'success');
      expect(r.code).toBe(0);
      expect(r.md).not.toMatch(/FAILED/);
      for (const s of tracked) expect(r.row(s.name), s.name).toContain('passed');
    });

    it.skipIf(!bash)(
      'names the install step and marks later steps "not run" on install failure',
      () => {
        const r = exec({
          'Install TypeScript dependencies': 'failure',
          'Verify (TypeScript half)': 'skipped',
          'Web production build': 'skipped',
        });
        expect(r.code).toBe(0);
        expect(r.md).toContain('FAILED at step "Install TypeScript dependencies"');
        expect(r.row('Install TypeScript dependencies')).toMatch(/failed/);
        expect(r.row('Verify (TypeScript half)')).toContain('not run');
        expect(r.row('Web production build')).toContain('not run');
      },
    );

    it.skipIf(!bash)('names the verify step when verify fails', () => {
      const r = exec({ 'Verify (TypeScript half)': 'failure', 'Web production build': 'skipped' });
      expect(r.code).toBe(0);
      expect(r.md).toContain('FAILED at step "Verify (TypeScript half)"');
      expect(r.md).not.toContain('FAILED at step "Install');
      expect(r.row('Web production build')).toContain('not run');
    });

    it.skipIf(!bash)('names the web build step when the build fails', () => {
      const r = exec({ 'Web production build': 'failure' });
      expect(r.code).toBe(0);
      expect(r.md).toContain('FAILED at step "Web production build"');
      expect(r.row('Verify (TypeScript half)')).toContain('passed');
    });

    it.skipIf(!bash)('names the Python install step when it fails', () => {
      const r = exec({
        'Install Python dependencies': 'failure',
        'Install TypeScript dependencies': 'skipped',
        'Verify (TypeScript half)': 'skipped',
        'Web production build': 'skipped',
      });
      expect(r.code).toBe(0);
      expect(r.md).toContain('FAILED at step "Install Python dependencies"');
    });

    it.skipIf(!bash)('names only the first failure when several steps fail', () => {
      const r = exec({ 'Verify (TypeScript half)': 'failure', 'Web production build': 'failure' });
      expect(r.code).toBe(0);
      expect(r.md).toContain('FAILED at step "Verify (TypeScript half)"');
      expect(r.md).not.toContain('FAILED at step "Web production build"');
    });

    it.skipIf(!bash)('names a timed-out (cancelled) verify step and shows its row', () => {
      const r = exec(
        { 'Verify (TypeScript half)': 'cancelled', 'Web production build': 'skipped' },
        'cancelled',
      );
      expect(r.code).toBe(0);
      expect(r.md).toContain('FAILED (cancelled) at step "Verify (TypeScript half)"');
      expect(r.row('Verify (TypeScript half)')).toContain('**cancelled** (timed out or cancelled)');
      expect(r.row('Web production build')).toContain('not run');
    });

    it.skipIf(!bash)('says a setup step failed when every tracked step was skipped', () => {
      const r = exec(
        {
          'Install Python dependencies': 'skipped',
          'Install TypeScript dependencies': 'skipped',
          'Verify (TypeScript half)': 'skipped',
          'Web production build': 'skipped',
        },
        'failure',
      );
      expect(r.code).toBe(0);
      expect(r.md).toContain('FAILED before "Install Python dependencies" (a setup step)');
      for (const s of tracked) expect(r.row(s.name), s.name).toContain('not run');
    });
  });
});

describe('S-1.8.3 AC2: guards so no check can escape the summary', () => {
  const beforeSummary = tsSteps.slice(
    0,
    tsSteps.findIndex((s) => s.name === 'Job summary'),
  );
  const summaryStep = tsSteps.find((s) => s.name === 'Job summary');

  it('gives every run step before the summary an id that the summary receives via env', () => {
    const runSteps = beforeSummary.filter((s) => s.run !== '');
    expect(runSteps.length).toBeGreaterThanOrEqual(4);
    for (const s of runSteps) {
      expect(s.id, `step "${s.name}" needs an id`).not.toBe('');
      expect(summaryStep?.env, s.name).toMatch(
        new RegExp(String.raw`^\s*\w+:\s*\$\{\{\s*steps\.${s.id}\.outcome\s*\}\}\s*$`, 'm'),
      );
    }
  });

  it('passes the job status to the summary through env', () => {
    expect(summaryStep?.env).toMatch(/^\s*JOB_STATUS:\s*\$\{\{\s*job\.status\s*\}\}\s*$/m);
  });

  it.each([
    ['api', apiJob],
    ['ts', tsJob],
  ])('does not use continue-on-error in the %s job', (_job, job) => {
    expect(job).not.toBe('');
    expect(job).not.toMatch(/continue-on-error:\s*(true|\$\{\{)/);
  });
});

describe('S-1.8.3 AC3: caching and time budget', () => {
  const idx = (pred: (s: Step) => boolean) => tsSteps.findIndex(pred);
  const pnpmIdx = idx((s) => s.uses.startsWith('pnpm/action-setup@'));
  const nodeIdx = idx((s) => s.uses.startsWith('actions/setup-node@'));
  const installIdx = idx((s) => /pnpm install/.test(s.run));
  const node = tsSteps[nodeIdx];

  it('caches the pnpm store through setup-node, keyed on pnpm-lock.yaml', () => {
    expect(node).toBeDefined();
    expect(node?.with).toMatch(/^\s*cache:\s*['"]?pnpm['"]?\s*$/m);
    expect(node?.with).toMatch(/^\s*cache-dependency-path:\s*['"]?pnpm-lock\.yaml['"]?\s*$/m);
    expect(existsSync(join(root, 'pnpm-lock.yaml'))).toBe(true);
  });

  it('puts pnpm on PATH (pnpm/action-setup) before setup-node and before install', () => {
    expect(pnpmIdx).toBeGreaterThanOrEqual(0);
    expect(nodeIdx).toBeGreaterThan(pnpmIdx);
    expect(installIdx).toBeGreaterThan(nodeIdx);
  });

  it('installs with --frozen-lockfile', () => {
    expect(tsSteps[installIdx]?.run).toMatch(/pnpm install --frozen-lockfile/);
  });

  it('caches uv keyed on apps/api/uv.lock', () => {
    const uv = tsSteps.find((s) => s.uses.startsWith('astral-sh/setup-uv@'));
    expect(uv).toBeDefined();
    expect(uv?.with).toMatch(/^\s*enable-cache:\s*true\s*$/m);
    expect(uv?.with).toMatch(/^\s*cache-dependency-glob:\s*['"]?apps\/api\/uv\.lock['"]?\s*$/m);
  });

  it('pins the same uv version as the api job', () => {
    const ver = (list: Step[]) =>
      /^\s*version:\s*['"]?([^\s'"]+)/m.exec(
        list.find((s) => s.uses.startsWith('astral-sh/setup-uv@'))?.with ?? '',
      )?.[1];
    expect(ver(tsSteps)).toBeDefined();
    expect(ver(tsSteps)).toBe(ver(steps));
  });

  it('has a job timeout of at most 12 minutes (the 6-minute cached run has headroom)', () => {
    const t = /^ {4}timeout-minutes:\s*(\d+)/m.exec(tsJob)?.[1];
    expect(t).toBeDefined();
    expect(Number(t)).toBeLessThanOrEqual(12);
  });
});
