/**
 * S-1.8.6 repo-side guard for .github/workflows/mobile-e2e.yml (the nightly Maestro run). No YAML
 * library is available at the root, so the workflow is read as text with a small indentation-aware
 * splitter (an absent block reads as empty, so tests fail closed).
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const root = join(import.meta.dirname, '..', '..');
const workflow = readFileSync(join(root, '.github/workflows/mobile-e2e.yml'), 'utf8').replace(
  /\r\n/g,
  '\n',
);

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

const jobsText = block(workflow, 'jobs', 0).join('\n');
const jobKeys = jobsText
  .split('\n')
  .filter((l) => isContent(l) && indentOf(l) === 2)
  .map((l) => l.trim().replace(/:.*$/, ''));
const jobText = block(jobsText, 'mobile-e2e', 2).join('\n');

/** Splits the job's `steps:` list on its `- ` item markers (list items sit at indent 6). */
function steps(): string[] {
  const items: string[][] = [];
  for (const l of block(jobText, 'steps', 4)) {
    if (/^\s*- /.test(l) && indentOf(l) === 6) items.push([l]);
    else items.at(-1)?.push(l);
  }
  return items.map((i) => i.join('\n'));
}
const stepWith = (re: RegExp): string => steps().find((s) => re.test(s)) ?? '';
const nonComment = (text: string) =>
  text
    .split('\n')
    .filter((l) => !l.trim().startsWith('#'))
    .join('\n');

describe('mobile e2e workflow (S-1.8.6)', () => {
  it('has least-privilege permissions: contents read only', () => {
    expect(
      block(workflow, 'permissions', 0)
        .filter(isContent)
        .map((l) => l.trim()),
    ).toEqual(['contents: read']);
  });

  describe('triggers', () => {
    const on = block(workflow, 'on', 0);

    it('runs on a schedule with a cron expression', () => {
      const schedule = block(on.join('\n'), 'schedule', 2).join('\n');
      expect(schedule).toMatch(/cron:\s*['"]?(\S+\s+){4}\S+['"]?/);
    });

    it('can be dispatched by hand', () => {
      expect(on.some((l) => indentOf(l) === 2 && l.trim().startsWith('workflow_dispatch'))).toBe(
        true,
      );
    });

    it('runs on pull requests that touch the flows or the workflow itself', () => {
      const pr = block(on.join('\n'), 'pull_request', 2).join('\n');
      expect(pr).toMatch(/^\s*-\s*['"]?apps\/mobile\/e2e\/\*\*['"]?\s*$/m);
      expect(pr).toMatch(/^\s*-\s*['"]?\.github\/workflows\/mobile-e2e\.yml['"]?\s*$/m);
    });
  });

  describe('the job', () => {
    it('is the only job and is named mobile-e2e', () => {
      expect(jobKeys).toEqual(['mobile-e2e']);
    });

    it('is bounded with timeout-minutes', () => {
      expect(jobText).toMatch(/^ {4}timeout-minutes:\s*\d+\s*$/m);
    });

    it('runs on ubuntu-24.04', () => {
      expect(jobText).toMatch(/^ {4}runs-on:\s*ubuntu-24\.04\s*$/m);
    });
  });

  describe('actions', () => {
    const uses = [...workflow.matchAll(/^\s*(?:- )?uses:\s*(\S+)(.*)$/gm)];

    it('pins every action to a 40-hex SHA with a version comment', () => {
      expect(uses.length).toBeGreaterThan(3);
      for (const m of uses) {
        expect(m[1], `uses ${m[1] ?? ''}`).toMatch(/^[\w.-]+\/[\w./-]+@[0-9a-f]{40}$/);
        expect(m[2], `comment on ${m[1] ?? ''}`).toMatch(/#\s*v\d/);
      }
    });

    it('uses the Android emulator runner, pinned', () => {
      const runner = uses.filter((m) =>
        /^reactivecircus\/android-emulator-runner@/i.test(m[1] ?? ''),
      );
      expect(runner).toHaveLength(1);
      expect(runner[0]?.[1]).toMatch(/@[0-9a-f]{40}$/);
    });
  });

  it('enables KVM for the emulator', () => {
    const kvm = stepWith(/\/dev\/kvm|99-kvm4all\.rules/);
    expect(kvm).not.toBe('');
  });

  it('masks the test password before it is used', () => {
    expect(nonComment(workflow)).toMatch(/::add-mask::/);
  });

  it('seeds the account through docker-entrypoint.sh', () => {
    const seed = stepWith(/app\.scripts\.seed_e2e_user/);
    expect(seed).toMatch(/docker-entrypoint\.sh\s+python -m app\.scripts\.seed_e2e_user/);
  });

  describe('the build and the run', () => {
    it('builds the release APK with the mobile e2e:build script', () => {
      expect(stepWith(/pnpm --filter @mtg-stats\/mobile e2e:build/)).not.toBe('');
    });

    it('runs the flows inside the emulator-runner script', () => {
      const runner = stepWith(/uses:\s*reactivecircus\/android-emulator-runner@/i);
      expect(runner).toMatch(/^\s*script:/m);
      const script = runner.slice(runner.search(/^\s*script:/m));
      expect(script).toMatch(/pnpm --filter @mtg-stats\/mobile e2e(?!:)/);
    });

    it('points the app at 10.0.2.2:8000 (or leaves the script default), never localhost', () => {
      const values = [
        ...nonComment(workflow).matchAll(/EXPO_PUBLIC_API_BASE_URL[:=]\s*(\S+)/g),
      ].map((m) => (m[1] ?? '').replace(/^['"]|['"]$/g, ''));
      for (const v of values) {
        expect(v).toBe('http://10.0.2.2:8000');
        expect(v).not.toMatch(/localhost|127\.0\.0\.1/);
      }
    });
  });

  describe('the Maestro CLI install', () => {
    const install = steps().filter((s) => /2\.12\.1/.test(s));

    it('pins version 2.12.1', () => {
      expect(install.length).toBeGreaterThan(0);
      expect(nonComment(install.join('\n'))).toMatch(/2\.12\.1/);
    });

    it('verifies a sha256 checksum', () => {
      expect(nonComment(install.join('\n'))).toMatch(/sha256sum|shasum\s+-a\s+256|sha256/i);
    });
  });

  describe('the failure artifact', () => {
    const upload = stepWith(/uses:\s*actions\/upload-artifact@/);

    it('is uploaded only when the job failed', () => {
      expect(upload).toMatch(/^\s*(?:- )?if:\s*\$\{\{\s*failure\(\)\s*\}\}\s*$/m);
    });

    it('is named maestro-output', () => {
      expect(upload).toMatch(/^\s*name:\s*maestro-output\s*$/m);
    });

    it('covers apps/mobile/e2e-output', () => {
      expect(upload).toMatch(/^\s*(?:path:\s*)?apps\/mobile\/e2e-output\/?\s*$/m);
    });
  });

  it('tears the Compose stack down even when the job fails', () => {
    const teardown = stepWith(/docker compose[^\n]*\bdown\b/);
    expect(teardown).toMatch(/^\s*(?:- )?if:\s*\$\{\{\s*always\(\)\s*\}\}\s*$/m);
  });
});
