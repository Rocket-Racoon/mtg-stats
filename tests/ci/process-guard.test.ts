/**
 * S-1.8.4 repo-side guards: the process-guard workflow (AC3), the pull request template (AC4) and
 * the convention statements in CONTRIBUTING.md and CLAUDE.md (AC2). AC1 (api and ts run on pull
 * requests and pushes to main) is already covered by ci-workflow.test.ts. No YAML library is
 * available at the root, so the workflow is read with the same small indentation-aware splitter.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const root = join(import.meta.dirname, '..', '..');
const read = (p: string) => readFileSync(join(root, p), 'utf8').replace(/\r\n/g, '\n');
const workflow = read('.github/workflows/process-guard.yml');

const indentOf = (line: string) => line.length - line.trimStart().length;
const isContent = (line: string) => line.trim() !== '' && !line.trim().startsWith('#');

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
const jobs = topLevel('jobs');
const jobIds = jobs
  .split('\n')
  .filter((l) => isContent(l) && indentOf(l) === 2)
  .map((l) => l.trim().replace(/:.*$/, ''));
const job = block(jobs, 'process-guard', 2).join('\n');
const code = workflow.replace(/^\s*#.*$/gm, '');

describe('AC3: the process-guard workflow triggers', () => {
  it('runs on pushes to main only', () => {
    const on = topLevel('on');
    const triggers = on
      .split('\n')
      .filter((l) => isContent(l) && indentOf(l) === 2)
      .map((l) => l.trim().replace(/:.*$/, ''));
    expect(triggers).toEqual(['push']);
    expect(block(on, 'push', 2).join('\n')).toMatch(
      /branches:\s*(\[\s*main\s*\]|\n\s*- main\s*$)/m,
    );
  });

  it('has exactly one job, named process-guard', () => {
    expect(jobIds).toEqual(['process-guard']);
    expect(job).toMatch(/^ {4}name:\s*process-guard\s*$/m);
    expect(job).toMatch(/^ {4}runs-on:\s*ubuntu-/m);
  });

  it('runs scripts/process_guard.py', () => {
    expect(job).toMatch(/python3?\s+scripts\/process_guard\.py/);
  });
});

describe('AC3: least privilege', () => {
  it('grants the workflow read-only contents permission and nothing else', () => {
    const perms = topLevel('permissions').split('\n').filter(isContent);
    expect(perms.map((l) => l.trim())).toEqual(['contents: read']);
  });

  it('gives the job issues: write and no other write permission', () => {
    const perms = block(job, 'permissions', 4)
      .filter(isContent)
      .map((l) => l.trim());
    expect(perms).toContain('issues: write');
    expect(perms.filter((p) => /:\s*write$/.test(p))).toEqual(['issues: write']);
    expect(perms).toContain('contents: read');
  });

  it('does not use pull_request_target or write permission at the workflow level', () => {
    expect(code).not.toMatch(/pull_request_target/);
    expect(topLevel('permissions')).not.toMatch(/write/);
  });
});

describe('AC3: injection hygiene and supply chain', () => {
  it('passes event values through env, never interpolated into a run script', () => {
    const runLines = code.split('\n').filter((l) => /^\s*(-\s*)?run:/.test(l));
    expect(runLines.length).toBeGreaterThan(0);
    for (const l of runLines) expect(l).not.toContain('${{');
    const env = block(job, 'env', 8).join('\n');
    for (const ev of ['github.event.before', 'github.event.after', 'github.event.forced']) {
      expect(env).toContain(ev);
    }
  });

  it('never interpolates a github.event expression outside env or with', () => {
    const lines = code.split('\n');
    const bad = lines.filter((l) => /\$\{\{\s*github\.event\./.test(l));
    // Every such line must be an env assignment of the form NAME: ${{ ... }}.
    for (const l of bad) expect(l.trim()).toMatch(/^[A-Z_]+:\s*\$\{\{[^}]+\}\}\s*$/);
  });

  it('gives gh a token through GH_TOKEN', () => {
    expect(job).toMatch(/GH_TOKEN:\s*\$\{\{\s*(github\.token|secrets\.GITHUB_TOKEN)\s*\}\}/);
  });

  it('pins every action to a 40-hex commit SHA with a version comment', () => {
    const uses = [...code.matchAll(/^\s*(?:-\s*)?uses:\s*(.+)$/gm)].map((m) => m[1] as string);
    expect(uses.length).toBeGreaterThanOrEqual(1);
    for (const u of uses) expect(u).toMatch(/^[\w.-]+\/[\w./-]+@[0-9a-f]{40}\s+# v\d/);
  });

  it('checks out with persist-credentials: false', () => {
    expect(job).toMatch(/^\s*persist-credentials:\s*false\s*$/m);
  });

  it('has a job timeout', () => {
    expect(job).toMatch(/^ {4}timeout-minutes:\s*\d+/m);
  });
});

describe('AC4: the pull request template', () => {
  const template = read('.github/pull_request_template.md');

  it('asks for the story ID', () => {
    expect(template).toMatch(/story/i);
    expect(template).toMatch(/S-\d/);
  });

  it('has a verify-gate checklist of unchecked boxes', () => {
    expect(template).toMatch(/verify/i);
    expect(template.match(/^- \[ \] /gm)?.length ?? 0).toBeGreaterThanOrEqual(3);
  });

  it('includes the pre-commit hooks ran, no --no-verify item', () => {
    const item = template.split('\n').find((l) => /^- \[ \]/.test(l) && /pre-commit/i.test(l));
    expect(item).toBeDefined();
    expect(item).toContain('--no-verify');
  });

  it('asks for the green api and ts checks and the code-reviewer review', () => {
    expect(template).toMatch(/`api`/);
    expect(template).toMatch(/`ts`/);
    expect(template).toContain('code-reviewer');
  });

  it.each([
    ['behavior changes', /^#+\s*behavior changes/im],
    ['migration notes', /^#+\s*migration notes/im],
    ['screenshots', /^#+\s*screenshots/im],
  ])('has a %s section', (_name, re) => {
    expect(template).toMatch(re);
  });
});

describe('AC2: the convention is documented', () => {
  const contributing = read('CONTRIBUTING.md');
  const claude = read('CLAUDE.md');
  const afterHeading = claude.slice(claude.indexOf('## Guardrails') + 3);
  const guardrails = afterHeading.split('\n## ')[0] as string;

  it.each([
    ['CONTRIBUTING.md', contributing],
    ['the Guardrails section of CLAUDE.md', guardrails],
  ])('%s states every rule of the convention', (_name, text) => {
    expect(text).toMatch(/pull request/i);
    expect(text).toMatch(/all work goes through a pull request/i);
    expect(text).toContain('`api`');
    expect(text).toContain('`ts`');
    expect(text).toMatch(/green/i);
    expect(text).toContain('code-reviewer');
    expect(text).toMatch(/push(ing)? directly to `main`/i);
    expect(text).toMatch(/force-push/i);
    expect(text).toMatch(/delet\w+ `main`/i);
  });

  it.each([
    ['CONTRIBUTING.md', contributing],
    ['the Guardrails section of CLAUDE.md', guardrails],
  ])('%s says plainly that GitHub does not enforce it', (_name, text) => {
    expect(text).toMatch(/GitHub (does not|doesn't|can't|cannot) enforce|GitHub does NOT enforce/i);
  });

  it('cross-references the process guard and its issue title in both places', () => {
    for (const text of [contributing, guardrails]) {
      expect(text).toContain('process-guard.yml');
      expect(text).toContain('Process bypass: <short SHA>');
    }
  });

  it('keeps the Guardrails section after the heading (the slice found it)', () => {
    expect(claude).toContain('## Guardrails');
    expect(guardrails.length).toBeGreaterThan(100);
  });
});
