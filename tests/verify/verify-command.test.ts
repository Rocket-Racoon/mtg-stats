/**
 * S-1.8.1 acceptance tests for scripts/verify.js. The real steps (uv, pytest, pnpm ...) are never
 * run: every step is replaced through --step-cmds / VERIFY_STEP_CMDS by a tiny node command that
 * appends the step name to a marker file. `--list` is used to check the real commands.
 *
 * Multi-command steps: an override may be an array of argv arrays, which run in order and stop at
 * the first failure; the "several commands per step" tests use that to prove a failing first
 * command skips the second.
 */
import { spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

const root = join(import.meta.dirname, '..', '..');
const script = join(root, 'scripts', 'verify.js');

const API_STEPS = [
  'secrets check',
  'ruff check',
  'ruff format --check',
  'mypy --strict',
  'pytest',
  'alembic check',
];
const TS_STEPS = [
  'client drift check',
  'eslint',
  'prettier --check',
  'tsc --noEmit',
  'vitest',
  'jest',
];
const ALL_STEPS = [...API_STEPS, ...TS_STEPS];

let dir: string;
let marker: string;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'verify-test-'));
  marker = join(dir, 'marker.txt');
});
afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

/**
 * The parent env with every key that case-insensitively matches an override removed, then the
 * overrides applied. Needed on Windows, where a Vitest worker may list `npm_execpath` as
 * `NPM_EXECPATH`: spreading both would leave two spellings and the child could see the wrong one.
 */
function childEnv(overrides: Record<string, string>): Record<string, string> {
  const drop = new Set(Object.keys(overrides).map((k) => k.toLowerCase()));
  const base: Record<string, string> = {};
  for (const [k, v] of Object.entries(process.env)) {
    if (v !== undefined && !drop.has(k.toLowerCase())) base[k] = v;
  }
  return { ...base, ...overrides };
}

function run(args: string[], env: Record<string, string> = {}, cwd = root) {
  const r = spawnSync(process.execPath, [script, ...args], {
    encoding: 'utf8',
    cwd,
    env: childEnv(env),
  });
  return { code: r.status, out: r.stdout, err: r.stderr };
}

/** A node argv that appends `name` to the marker file, then exits with `code`. */
function mark(name: string, code = 0): string[] {
  return [
    'node',
    '-e',
    'require("node:fs").appendFileSync(process.argv[1], process.argv[2] + "\\n");' +
      `process.exit(${code})`,
    marker,
    name,
  ];
}

function overrides(names: string[], codes: Record<string, number> = {}): string {
  return JSON.stringify(Object.fromEntries(names.map((n) => [n, mark(n, codes[n] ?? 0)])));
}

function ran(): string[] {
  return existsSync(marker)
    ? readFileSync(marker, 'utf8')
        .split(/\r?\n/)
        .filter((l) => l !== '')
    : [];
}

function listedNames(out: string): string[] {
  return [...out.matchAll(/^\[\d+\/\d+\] (.+)$/gm)].map((m) => m[1] as string);
}

function stepBlock(out: string, name: string): string {
  const lines = out.split(/\r?\n/);
  const start = lines.findIndex((l) => l.endsWith(`] ${name}`));
  const rest = lines.slice(start + 1);
  const end = rest.findIndex((l) => /^\[\d+\/\d+\] /.test(l));
  return (end === -1 ? rest : rest.slice(0, end)).join('\n');
}

describe('AC1: full run order and commands', () => {
  it('lists exactly the twelve steps in acceptance-criterion order', () => {
    const r = run(['--list']);
    expect(r.code).toBe(0);
    expect(listedNames(r.out)).toEqual(ALL_STEPS);
  });

  it('maps each step to the tool named in the criterion', () => {
    const out = run(['--list']).out;
    expect(stepBlock(out, 'secrets check')).toContain('scripts/secrets_check.py');
    expect(stepBlock(out, 'ruff check')).toContain('ruff check');
    expect(stepBlock(out, 'ruff format --check')).toContain('ruff format --check');
    expect(stepBlock(out, 'mypy --strict')).toContain('mypy --strict app/');
    expect(stepBlock(out, 'pytest')).toContain('pytest --cov=app --cov-fail-under=85');
    expect(stepBlock(out, 'alembic check')).toContain('alembic check');
    expect(stepBlock(out, 'client drift check')).toContain('pnpm client:check');
    expect(stepBlock(out, 'eslint')).toContain('pnpm lint');
    expect(stepBlock(out, 'prettier --check')).toContain('pnpm format:check');
    expect(stepBlock(out, 'tsc --noEmit')).toContain('pnpm typecheck');
    expect(stepBlock(out, 'vitest')).toContain('vitest');
    expect(stepBlock(out, 'jest')).toContain('@mtg-stats/mobile');
  });

  it('runs Vitest with a single fork worker to suit memory-constrained machines', () => {
    const block = stepBlock(run(['--list']).out, 'vitest');
    expect(block).toContain('--pool=forks');
    expect(block).toContain('--maxWorkers=1');
  });

  it('points the root verify scripts at the script with the right --only', () => {
    const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8')) as {
      scripts: Record<string, string>;
    };
    expect(pkg.scripts['verify']).toBe('node scripts/verify.js');
    expect(pkg.scripts['verify:api']).toBe('node scripts/verify.js --only api');
    expect(pkg.scripts['verify:ts']).toBe('node scripts/verify.js --only ts');
  });

  it('runs all twelve steps in order, exits 0 and reports success with a duration table', () => {
    const r = run(['--step-cmds', overrides(ALL_STEPS)]);
    expect(r.code).toBe(0);
    expect(ran()).toEqual(ALL_STEPS);
    expect(r.out).toContain('verify: all steps passed');
    for (const name of ALL_STEPS) expect(r.out).toContain(name);
    expect(r.out).toMatch(/\d+\.\ds/);
  });
});

describe('AC2: a failing step stops the run', () => {
  const cases: [string, number][] = [
    ['secrets check', 3],
    ['mypy --strict', 1],
    ['alembic check', 4],
    ['client drift check', 5],
    ['prettier --check', 9],
    ['jest', 7],
  ];

  it.each(cases)('stops at "%s", exits %i, names the step and skips later steps', (name, code) => {
    const r = run(['--step-cmds', overrides(ALL_STEPS, { [name]: code })]);
    expect(r.code).toBe(code);
    expect(r.err).toContain(`verify: FAILED at step "${name}"`);
    expect(r.err).toContain(`exit ${code}`);
    expect(r.out).not.toContain('all steps passed');
    expect(ran()).toEqual(ALL_STEPS.slice(0, ALL_STEPS.indexOf(name) + 1));
  });

  it('stops inside a single half too', () => {
    const r = run(['--only', 'ts', '--step-cmds', overrides(TS_STEPS, { eslint: 2 })]);
    expect(r.code).toBe(2);
    expect(r.err).toContain('FAILED at step "eslint"');
    expect(ran()).toEqual(['client drift check', 'eslint']);
  });

  it('reports a command that cannot start with the step name and a non-zero exit', () => {
    const cmds = JSON.parse(overrides(ALL_STEPS)) as Record<string, unknown>;
    cmds['ruff check'] = ['definitely-not-a-real-executable-xyz'];
    const r = run(['--step-cmds', JSON.stringify(cmds)]);
    expect(r.code).not.toBe(0);
    expect(r.err).toContain('FAILED at step "ruff check"');
    expect(r.err).toContain('could not start');
    expect(ran()).toEqual(['secrets check']);
  });

  it('adds the alembic upgrade hint only when alembic check fails', () => {
    const alembic = run(['--step-cmds', overrides(ALL_STEPS, { 'alembic check': 1 })]);
    expect(alembic.err).toContain('alembic upgrade head');
    const other = run(['--step-cmds', overrides(ALL_STEPS, { pytest: 1 })]);
    expect(other.err).not.toContain('alembic upgrade head');
  });

  it('lists multi-command steps with several sub-commands (structure only)', () => {
    const out = run(['--list']).out;
    const count = (name: string) =>
      stepBlock(out, name)
        .split(/\r?\n/)
        .filter((l) => l.trim() !== '').length;
    expect(count('ruff check')).toBe(2);
    expect(count('vitest')).toBeGreaterThan(1);
  });
});

describe('AC3: verify:api and verify:ts select one half', () => {
  it('--only api runs exactly steps 1 to 6', () => {
    const r = run(['--only', 'api', '--step-cmds', overrides(ALL_STEPS)]);
    expect(r.code).toBe(0);
    expect(ran()).toEqual(API_STEPS);
  });

  it('--only ts runs exactly steps 7 to 12', () => {
    const r = run(['--only', 'ts', '--step-cmds', overrides(ALL_STEPS)]);
    expect(r.code).toBe(0);
    expect(ran()).toEqual(TS_STEPS);
  });

  it('--list --only api shows only the Python steps', () => {
    expect(listedNames(run(['--list', '--only', 'api']).out)).toEqual(API_STEPS);
  });

  it('--list --only ts shows only the TypeScript steps', () => {
    expect(listedNames(run(['--list', '--only', 'ts']).out)).toEqual(TS_STEPS);
  });

  it('rejects an invalid --only value with exit 2', () => {
    const r = run(['--only', 'python']);
    expect(r.code).toBe(2);
    expect(r.err).toContain('--only');
  });
});

describe('AC4: cross-platform behavior', () => {
  it('delivers tricky arguments unchanged to a lone overridden step', () => {
    const out = join(dir, 'argv.json');
    const tricky = [
      'with space',
      'say "quoted"',
      "it's",
      'a & b',
      '%PATH%',
      '$HOME',
      '$(echo hi)',
      '*',
    ];
    const noop = ['node', '-e', '0'];
    const cmds = Object.fromEntries(TS_STEPS.map((n) => [n, noop]));
    cmds['eslint'] = [
      'node',
      '-e',
      'require("node:fs").writeFileSync(process.argv[1], JSON.stringify(process.argv.slice(2)))',
      out,
      ...tricky,
    ];
    const r = run(['--only', 'ts', '--step-cmds', JSON.stringify(cmds)]);
    expect(r.code).toBe(0);
    expect(JSON.parse(readFileSync(out, 'utf8'))).toEqual(tricky);
  });

  it('treats "node" as the current Node binary even with an empty PATH', () => {
    const r = run(['--step-cmds', overrides(ALL_STEPS)], { PATH: '' });
    expect(r.code).toBe(0);
    expect(ran()).toEqual(ALL_STEPS);
  });

  it('runs overrides from the repo root whatever the caller cwd is', () => {
    const cwdMark = [
      'node',
      '-e',
      'require("node:fs").appendFileSync(process.argv[1], process.cwd() + "\\n")',
      marker,
    ];
    const cmds = Object.fromEntries(ALL_STEPS.map((n) => [n, cwdMark]));
    const r = run(['--step-cmds', JSON.stringify(cmds)], {}, dir);
    expect(r.code).toBe(0);
    const cwds = ran();
    expect(cwds).toHaveLength(ALL_STEPS.length);
    for (const cwd of cwds) expect(cwd.toLowerCase()).toBe(root.toLowerCase());
  });

  it('never enables shell mode in the script source', () => {
    const src = readFileSync(script, 'utf8');
    expect(src).not.toMatch(/shell\s*:\s*true/);
    expect(src).not.toMatch(/\bexecSync\b/);
  });

  // Only provable in CI on Linux/macOS (and by hand in PowerShell): that a real `pnpm verify`
  // with the Compose stack up behaves identically on each OS.
});

describe('usage errors exit 2', () => {
  it('rejects an unknown flag', () => {
    const r = run(['--bogus']);
    expect(r.code).toBe(2);
    expect(r.err).toContain('--bogus');
  });

  it('rejects --only without a value', () => {
    expect(run(['--only']).code).toBe(2);
  });

  it('rejects --step-cmds without a value', () => {
    expect(run(['--step-cmds']).code).toBe(2);
  });

  it('rejects invalid JSON in --step-cmds', () => {
    expect(run(['--step-cmds', '{not json']).code).toBe(2);
  });

  it('rejects --step-cmds that is not an object of non-empty argv arrays', () => {
    expect(run(['--step-cmds', '[]']).code).toBe(2);
    expect(run(['--step-cmds', '{"eslint":"node"}']).code).toBe(2);
    expect(run(['--step-cmds', '{"eslint":[]}']).code).toBe(2);
  });

  it('rejects an unknown step name and lists the known ones', () => {
    const r = run(['--step-cmds', '{"nope":["node","-e","0"]}']);
    expect(r.code).toBe(2);
    expect(r.err).toContain('nope');
    expect(r.err).toContain('secrets check');
  });

  it('rejects bad JSON in VERIFY_STEP_CMDS', () => {
    expect(run([], { VERIFY_STEP_CMDS: '{oops' }).code).toBe(2);
  });
});

describe('VERIFY_STEP_CMDS environment variable', () => {
  it('applies overrides from the environment', () => {
    const r = run([], { VERIFY_STEP_CMDS: overrides(ALL_STEPS) });
    expect(r.code).toBe(0);
    expect(ran()).toEqual(ALL_STEPS);
  });

  it('lets the --step-cmds flag win over the environment', () => {
    const r = run(['--only', 'api', '--step-cmds', overrides(ALL_STEPS, { pytest: 6 })], {
      VERIFY_STEP_CMDS: overrides(ALL_STEPS),
    });
    expect(r.code).toBe(6);
    expect(r.err).toContain('FAILED at step "pytest"');
  });
});

describe('every filter command fails when it matches no workspace', () => {
  it('passes --fail-if-no-match on every pnpm --filter command', () => {
    const lines = run(['--list'])
      .out.split(/\r?\n/)
      .filter((l) => l.includes('--filter'));
    expect(lines.length).toBeGreaterThan(0);
    for (const line of lines) expect(line).toContain('--fail-if-no-match');
  });

  it('names every workspace with a test script exactly once, in the right test step', () => {
    const yaml = readFileSync(join(root, 'pnpm-workspace.yaml'), 'utf8');
    const globs = [...yaml.matchAll(/^\s+-\s+(\S+)\s*$/gm)].map((m) =>
      (m[1] as string).replace(/^["']|["']$/g, ''),
    );
    expect(globs.length).toBeGreaterThan(0);
    const dirs: string[] = [];
    for (const g of globs) {
      if (!/^[\w.-]+(\/[\w.-]+)*(\/\*)?$/.test(g)) {
        throw new Error(`unsupported workspace glob "${g}": extend this test to expand it`);
      }
      if (g.endsWith('/*')) {
        const base = g.slice(0, -2);
        for (const e of readdirSync(join(root, base), { withFileTypes: true })) {
          if (e.isDirectory()) dirs.push(join(base, e.name));
        }
      } else dirs.push(g);
    }
    const tested: { name: string; jest: boolean }[] = [];
    for (const d of dirs) {
      const file = join(root, d, 'package.json');
      if (!existsSync(file)) continue;
      const pkg = JSON.parse(readFileSync(file, 'utf8')) as {
        name: string;
        scripts?: Record<string, string>;
      };
      const test = pkg.scripts?.['test'];
      if (test) tested.push({ name: pkg.name, jest: /\bjest\b/.test(test) });
    }
    expect(tested.length).toBeGreaterThan(0);

    const out = run(['--list']).out;
    const vitest = stepBlock(out, 'vitest');
    const jest = stepBlock(out, 'jest');
    const count = (text: string, name: string) =>
      text.split(/\r?\n/).filter((l) => new RegExp(`--filter ${name}( |$)`).test(l)).length;
    for (const { name, jest: isJest } of tested) {
      expect(count(isJest ? jest : vitest, name), name).toBe(1);
      expect(count(isJest ? vitest : jest, name), name).toBe(0);
    }
    const filters = [...(vitest + jest).matchAll(/--filter (\S+)/g)].map((m) => m[1]);
    expect(filters.sort()).toEqual(tested.map((t) => t.name).sort());
  });

  it('runs the root tests/ Vitest suite exactly once', () => {
    const lines = stepBlock(run(['--list']).out, 'vitest')
      .split(/\r?\n/)
      .filter((l) => l.includes('exec vitest run --dir tests'));
    expect(lines).toHaveLength(1);
  });
});

describe('pnpm resolution through npm_execpath', () => {
  const only = (keep: string) => overrides(ALL_STEPS.filter((n) => n !== keep));

  it('runs a pnpm-named JS file under Node with the step arguments', () => {
    const out = join(dir, 'pnpm-argv.json');
    const fake = join(dir, 'pnpm-fake.cjs');
    writeFileSync(
      fake,
      'require("node:fs").writeFileSync(process.env.FAKE_OUT, JSON.stringify(process.argv.slice(2)));',
    );
    const r = run(['--step-cmds', only('client drift check')], {
      npm_execpath: fake,
      FAKE_OUT: out,
    });
    expect(r.code).toBe(0);
    expect(JSON.parse(readFileSync(out, 'utf8'))).toEqual(['client:check']);
  });

  it('ignores an npm_execpath that is not pnpm', () => {
    const npmCli = join(dir, 'npm-cli.js');
    const npmRan = join(dir, 'npm-ran.txt');
    writeFileSync(npmCli, `require("node:fs").writeFileSync(${JSON.stringify(npmRan)}, "ran");`);
    const r = run(['--step-cmds', only('client drift check')], {
      npm_execpath: npmCli,
      PATH: dir,
    });
    expect(r.code).not.toBe(0);
    expect(r.err).toContain('FAILED at step "client drift check"');
    expect(existsSync(npmRan)).toBe(false);
    if (process.platform === 'win32') expect(r.err).toContain('cannot locate pnpm');
    else expect(r.err).toContain('could not start');
  });

  it.runIf(process.platform === 'win32')(
    'fails with a clear message on Windows when npm_execpath is empty',
    () => {
      const r = run(['--step-cmds', only('client drift check')], { npm_execpath: '' });
      expect(r.code).not.toBe(0);
      expect(r.err).toContain('FAILED at step "client drift check"');
      expect(r.err).toContain('cannot locate pnpm');
    },
  );
});

describe('several commands per step', () => {
  it('stops a step at its first failing command and exits with that code', () => {
    const cmds = JSON.parse(overrides(ALL_STEPS)) as Record<string, unknown>;
    cmds['eslint'] = [mark('first', 3), mark('second')];
    const r = run(['--step-cmds', JSON.stringify(cmds)]);
    expect(r.code).toBe(3);
    expect(r.err).toContain('FAILED at step "eslint"');
    expect(ran()).toEqual([...ALL_STEPS.slice(0, ALL_STEPS.indexOf('eslint')), 'first']);
  });

  it('runs every command of a step in order when all pass', () => {
    const cmds = JSON.parse(overrides(ALL_STEPS)) as Record<string, unknown>;
    cmds['pytest'] = [mark('one'), mark('two')];
    const r = run(['--step-cmds', JSON.stringify(cmds)]);
    expect(r.code).toBe(0);
    const expected = ALL_STEPS.flatMap((n) => (n === 'pytest' ? ['one', 'two'] : [n]));
    expect(ran()).toEqual(expected);
  });

  it.each([['[[]]'], ['[["node"],[]]'], ['[["node"],"x"]'], ['[]'], ['[1]']])(
    'rejects the invalid shape %s with exit 2',
    (shape) => {
      const r = run(['--step-cmds', `{"eslint":${shape}}`]);
      expect(r.code).toBe(2);
      expect(existsSync(marker)).toBe(false);
    },
  );
});

describe('argument handling', () => {
  it('ignores a bare -- argument', () => {
    const r = run(['--', '--list']);
    expect(r.code).toBe(0);
    expect(listedNames(r.out)).toEqual(ALL_STEPS);
  });

  it('rejects a repeated --only with exit 2', () => {
    const r = run(['--only', 'api', '--only', 'ts', '--list']);
    expect(r.code).toBe(2);
    expect(r.err).toContain('--only');
  });
});
