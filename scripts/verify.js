// Local verify gate (S-1.8.1): runs the same checks CI runs, in a fixed order, and stops at the
// first failing step.
//
//   node scripts/verify.js [--only api|ts] [--list] [--step-cmds <json object>]
//
//   --only api|ts        run only the Python half (steps 1-6) or the TypeScript half (steps 7-12)
//   --list               print the selected steps and their commands, run nothing
//   --step-cmds <json>   JSON object mapping a step name to an argv array that replaces that
//                        step's commands (run from the repo root); also read from
//                        VERIFY_STEP_CMDS, the flag wins. Meant for tests, for example
//                        {"mypy --strict": ["node", "-e", "process.exit(1)"]}. A first element of
//                        "node" means this Node binary.
//
// Python steps run through `uv` (a real executable). pnpm is a `.cmd` shim on Windows that cannot
// be spawned without a shell, so pnpm steps run through `$npm_execpath` (a JS file is run with this
// Node binary, a native pnpm binary directly); that variable is set when this script is started
// through `pnpm verify`.
//
// Exit codes: 0 every selected step passed; 1 a step failed (its name is printed to stderr);
// 2 bad usage.
import { spawnSync } from 'node:child_process';
import { dirname, resolve } from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const EXIT_FAILED = 1;
const EXIT_USAGE = 2;
const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const API = 'apps/api';
const ALEMBIC_HINT =
  'Hint: the dev database must be at head. Run `uv run alembic upgrade head` from apps/api (dev database only).';

const uv = (cwd, ...args) => ({ tool: 'uv', args, cwd });
const pnpm = (...args) => ({ tool: 'pnpm', args, cwd: '.' });
const vitestFlags = ['--pool=forks', '--maxWorkers=1'];

// Order matters: it is the order of acceptance criterion 1. Test steps run one workspace at a
// time on purpose (memory-constrained machines).
export const STEPS = [
  {
    name: 'secrets check',
    half: 'api',
    commands: [uv('.', 'run', '--project', API, 'python', 'scripts/secrets_check.py')],
  },
  {
    name: 'ruff check',
    half: 'api',
    commands: [
      uv(API, 'run', 'ruff', 'check', '.'),
      uv(API, 'run', 'ruff', 'check', '../../scripts'),
    ],
  },
  {
    name: 'ruff format --check',
    half: 'api',
    commands: [
      uv(API, 'run', 'ruff', 'format', '--check', '.'),
      uv(API, 'run', 'ruff', 'format', '--check', '../../scripts'),
    ],
  },
  {
    name: 'mypy --strict',
    half: 'api',
    commands: [
      uv(API, 'run', 'mypy', '--strict', 'app/'),
      uv(API, 'run', 'mypy', '--strict', '../../scripts'),
    ],
  },
  {
    name: 'pytest',
    half: 'api',
    commands: [
      uv(API, 'run', 'pytest', '--cov=app', '--cov-fail-under=85'),
      uv(API, 'run', 'pytest', '-c', 'pyproject.toml', '../../scripts/tests'),
    ],
  },
  {
    name: 'alembic check',
    half: 'api',
    hint: ALEMBIC_HINT,
    commands: [uv(API, 'run', 'alembic', 'check')],
  },
  { name: 'client drift check', half: 'ts', commands: [pnpm('client:check')] },
  { name: 'eslint', half: 'ts', commands: [pnpm('lint')] },
  { name: 'prettier --check', half: 'ts', commands: [pnpm('format:check')] },
  { name: 'tsc --noEmit', half: 'ts', commands: [pnpm('typecheck')] },
  {
    name: 'vitest',
    half: 'ts',
    commands: [
      pnpm('--filter', '@mtg-stats/web', 'test'),
      pnpm('--filter', '@mtg-stats/api-client', 'test', ...vitestFlags),
      pnpm('--filter', '@mtg-stats/game-core', 'test'),
      pnpm('--filter', '@mtg-stats/card-utils', 'test'),
      pnpm('exec', 'vitest', 'run', '--dir', 'tests', ...vitestFlags),
    ],
  },
  {
    name: 'jest',
    half: 'ts',
    commands: [pnpm('--filter', '@mtg-stats/mobile', 'test', '--maxWorkers=2')],
  },
];

class UsageError extends Error {}

export function parseArgs(argv, env) {
  const opts = { only: undefined, list: false, stepCmds: env.VERIFY_STEP_CMDS };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === '--list') {
      opts.list = true;
    } else if (arg === '--only' || arg === '--step-cmds') {
      const value = argv[++i];
      if (value === undefined || value.startsWith('--')) {
        throw new UsageError(`${arg} needs a value`);
      }
      if (arg === '--only') opts.only = value;
      else opts.stepCmds = value;
    } else {
      throw new UsageError(`Unknown argument: ${arg}`);
    }
  }
  if (opts.only !== undefined && opts.only !== 'api' && opts.only !== 'ts') {
    throw new UsageError('--only must be "api" or "ts"');
  }
  return { only: opts.only, list: opts.list, overrides: parseOverrides(opts.stepCmds) };
}

function parseOverrides(text) {
  if (!text) return {};
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    parsed = null;
  }
  const valid =
    parsed !== null &&
    typeof parsed === 'object' &&
    !Array.isArray(parsed) &&
    Object.values(parsed).every(
      (v) => Array.isArray(v) && v.length > 0 && v.every((s) => typeof s === 'string'),
    );
  if (!valid) {
    throw new UsageError('--step-cmds must be a JSON object of step name -> non-empty argv array');
  }
  const unknown = Object.keys(parsed).filter((name) => !STEPS.some((s) => s.name === name));
  if (unknown.length > 0) {
    const known = STEPS.map((s) => s.name).join(', ');
    throw new UsageError(`--step-cmds names unknown steps: ${unknown.join(', ')}. Known: ${known}`);
  }
  return parsed;
}

// Turn a command description into something spawnSync can run without a shell.
function resolveCommand(command, env) {
  if (command.argv) {
    const [file, ...args] = command.argv;
    return { file: file === 'node' ? process.execPath : file, args, cwd: repoRoot };
  }
  const cwd = resolve(repoRoot, command.cwd);
  if (command.tool === 'pnpm') {
    const execPath = env.npm_execpath;
    if (execPath) {
      // npm_execpath is a JS entry point for npm-installed pnpm, or a native binary for the
      // standalone pnpm (`@pnpm/exe`).
      const isScript = /\.[cm]?js$/i.test(execPath);
      return isScript
        ? { file: process.execPath, args: [execPath, ...command.args], cwd }
        : { file: execPath, args: command.args, cwd };
    }
    if (process.platform === 'win32') {
      throw new Error(
        'cannot locate pnpm: npm_execpath is not set. Start the gate with `pnpm verify` (not `node scripts/verify.js`).',
      );
    }
    return { file: 'pnpm', args: command.args, cwd };
  }
  return { file: command.tool, args: command.args, cwd };
}

function commandsFor(step, overrides) {
  return overrides[step.name] ? [{ argv: overrides[step.name] }] : step.commands;
}

function describe(command) {
  if (command.argv) return command.argv.join(' ');
  const where = command.cwd && command.cwd !== '.' ? `   (in ${command.cwd})` : '';
  return `${command.tool} ${command.args.join(' ')}${where}`;
}

export function selectSteps(only) {
  return STEPS.filter((s) => only === undefined || s.half === only);
}

function runStep(step, overrides, env) {
  for (const command of commandsFor(step, overrides)) {
    const shown = describe(command);
    let resolved;
    try {
      resolved = resolveCommand(command, env);
    } catch (error) {
      return { code: EXIT_FAILED, reason: error.message };
    }
    console.log(`$ ${shown}`);
    const result = spawnSync(resolved.file, resolved.args, {
      cwd: resolved.cwd,
      stdio: 'inherit',
    });
    if (result.error) {
      return {
        code: EXIT_FAILED,
        reason: `could not start \`${shown}\`: ${result.error.message}`,
      };
    }
    if (result.status !== 0) {
      const how =
        result.status === null ? `was killed by ${result.signal}` : `exited ${result.status}`;
      return { code: result.status ?? EXIT_FAILED, reason: `\`${shown}\` ${how}` };
    }
  }
  return null;
}

function main() {
  const { only, list, overrides } = parseArgs(process.argv.slice(2), process.env);
  const steps = selectSteps(only);
  if (list) {
    steps.forEach((step, i) => {
      console.log(`[${i + 1}/${steps.length}] ${step.name}`);
      for (const command of commandsFor(step, overrides)) console.log(`    ${describe(command)}`);
    });
    return 0;
  }
  const timings = [];
  for (const [i, step] of steps.entries()) {
    console.log(`\n== [${i + 1}/${steps.length}] ${step.name}`);
    const started = Date.now();
    const failure = runStep(step, overrides, process.env);
    const seconds = (Date.now() - started) / 1000;
    if (failure) {
      console.error(
        `\nverify: FAILED at step "${step.name}" (exit ${failure.code}): ${failure.reason}`,
      );
      if (step.hint) console.error(step.hint);
      return Math.min(failure.code || EXIT_FAILED, 255);
    }
    timings.push([step.name, seconds]);
  }
  console.log('\nverify: all steps passed');
  for (const [name, seconds] of timings) console.log(`  ${name.padEnd(22)} ${seconds.toFixed(1)}s`);
  return 0;
}

try {
  process.exitCode = main();
} catch (error) {
  if (!(error instanceof UsageError)) throw error;
  console.error(`verify: ${error.message}`);
  process.exitCode = EXIT_USAGE;
}
