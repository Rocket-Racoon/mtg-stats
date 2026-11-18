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

const mobilePackage = JSON.parse(readFileSync(join(root, 'apps/mobile/package.json'), 'utf8')) as {
  scripts: Record<string, string>;
};

describe('mobile e2e workflow (S-1.8.6)', () => {
  it('ties the e2e script output folder to the artifact upload path', () => {
    const script = mobilePackage.scripts.e2e ?? '';
    expect(script).toMatch(/^maestro test e2e(\s|$)/);
    expect(script).toMatch(/--test-output-dir\s+e2e-output(\s|$)/);
    const upload = stepWith(/uses:\s*actions\/upload-artifact@/);
    expect(upload).toMatch(/^\s*(?:path:\s*)?apps\/mobile\/e2e-output\/?\s*$/m);
  });

  it('runs the flows from one single-line runner script with a logcat fallback', () => {
    const runner = steps().filter(
      (s) =>
        /uses:\s*reactivecircus\/android-emulator-runner@/i.test(s) &&
        /pnpm --filter @mtg-stats\/mobile e2e(?!:)/.test(s),
    )[0];
    const scriptLine = /^\s*script:[ \t]*(.*)$/m.exec(runner ?? '')?.[1] ?? '';
    // A block scalar (| or >) would be run line by line with sh -c.
    expect(scriptLine).not.toMatch(/^[|>]/);
    expect(scriptLine).toMatch(/pnpm --filter @mtg-stats\/mobile e2e(?!:)/);
    expect(scriptLine).toMatch(/adb logcat/);
    expect(scriptLine).toMatch(/exit 1/);
  });

  it('has no continue-on-error anywhere', () => {
    expect(nonComment(workflow)).not.toMatch(/continue-on-error/);
  });

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

    it('uses the Android emulator runner, every use pinned', () => {
      const runner = uses.filter((m) =>
        /^reactivecircus\/android-emulator-runner@/i.test(m[1] ?? ''),
      );
      expect(runner.length).toBeGreaterThanOrEqual(1);
      for (const m of runner) {
        expect(m[1]).toMatch(/@[0-9a-f]{40}$/);
        expect(m[2]).toMatch(/#\s*v\d/);
      }
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

    it('runs the flows in exactly one always-running emulator-runner step', () => {
      const runners = steps().filter((s) =>
        /uses:\s*reactivecircus\/android-emulator-runner@/i.test(s),
      );
      const running = runners.filter((r) => {
        const at = r.search(/^\s*script:/m);
        return at !== -1 && /pnpm --filter @mtg-stats\/mobile e2e(?!:)/.test(r.slice(at));
      });
      expect(running).toHaveLength(1);
      expect(running[0]).not.toMatch(/^\s*(?:- )?if:/m);
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
    // The version and checksum may sit in a workflow-level env block (MAESTRO_VERSION, MAESTRO_SHA256)
    // that the install step references, or be written inline in the step.
    const install = nonComment(
      steps()
        .filter((s) => /maestro\.zip|releases\/download\/cli-/i.test(s))
        .join('\n'),
    );
    const all = nonComment(workflow);

    it('downloads the pinned version 2.12.1', () => {
      expect(install).not.toBe('');
      const literal = /cli-2\.12\.1/.test(install);
      const viaEnv =
        /cli-\$\{?(\w+)\}?/.test(install) &&
        new RegExp(
          `^\\s*${/cli-\$\{?(\w+)\}?/.exec(install)?.[1] ?? '_'}:\\s*['"]?2\\.12\\.1['"]?\\s*$`,
          'm',
        ).test(all);
      expect(literal || viaEnv).toBe(true);
    });

    it('verifies a sha256 checksum of the download', () => {
      expect(install).toMatch(/sha256sum\s+-c|shasum\s+-a\s+256\s+-c/);
      expect(all).toMatch(/\b[0-9a-f]{64}\b/);
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
