/**
 * S-1.8.5 repo-side guards for .github/workflows/e2e.yml, apps/web/playwright.config.ts and the
 * ESLint ban on CSS selectors in apps/web/e2e/. No YAML library is available at the root, so the
 * workflow is read as text with a small indentation-aware splitter (an absent block reads as
 * empty, so tests fail closed).
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { ESLint } from 'eslint';

const root = join(import.meta.dirname, '..', '..');
const read = (p: string) => readFileSync(join(root, p), 'utf8').replace(/\r\n/g, '\n');
const workflow = read('.github/workflows/e2e.yml');
const config = read('apps/web/playwright.config.ts');

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

const jobText = block(block(workflow, 'jobs', 0).join('\n'), 'e2e', 2).join('\n');

/** Splits the job's `steps:` list on its `- ` item markers (list items sit at indent 6). */
function steps(): string[] {
  const items: string[][] = [];
  for (const l of block(jobText, 'steps', 4)) {
    if (/^\s*- /.test(l) && indentOf(l) === 6) items.push([l]);
    else items.at(-1)?.push(l);
  }
  return items.map((i) => i.join('\n'));
}
const step = (name: string): string =>
  steps().find((s) => new RegExp(`name:\\s*${name}`).test(s)) ?? '';

describe('e2e workflow (S-1.8.5)', () => {
  it('has least-privilege permissions: contents read only', () => {
    expect(
      block(workflow, 'permissions', 0)
        .filter(isContent)
        .map((l) => l.trim()),
    ).toEqual(['contents: read']);
  });

  it('bounds the job with timeout-minutes', () => {
    expect(jobText).toMatch(/^ {4}timeout-minutes:\s*\d+\s*$/m);
  });

  it('pins every action to a 40-hex SHA with a version comment', () => {
    const uses = [...workflow.matchAll(/^\s*(?:- )?uses:\s*(\S+)(.*)$/gm)];
    expect(uses.length).toBeGreaterThan(3);
    for (const m of uses) {
      expect(m[1], `uses ${m[1] ?? ''}`).toMatch(/^[\w.-]+\/[\w./-]+@[0-9a-f]{40}$/);
      expect(m[2], `comment on ${m[1] ?? ''}`).toMatch(/#\s*v\d/);
    }
  });

  it('checks out without persisting credentials', () => {
    const checkout = steps().find((s) => /uses:\s*actions\/checkout@/.test(s)) ?? '';
    expect(checkout).toMatch(/persist-credentials:\s*false/);
  });

  it('seeds the account through docker-entrypoint.sh', () => {
    const seed = step('Seed the test account');
    expect(seed).toMatch(/docker-entrypoint\.sh\s+python -m app\.scripts\.seed_e2e_user/);
  });

  describe('the artifact upload', () => {
    const upload = steps().find((s) => /uses:\s*actions\/upload-artifact@/.test(s)) ?? '';

    it('runs only when the job failed', () => {
      expect(upload).toMatch(/^\s*if:\s*\$\{\{\s*failure\(\)\s*\}\}\s*$/m);
    });

    it('keeps the artifact 7 days', () => {
      expect(upload).toMatch(/retention-days:\s*7\s*$/m);
    });

    it('uploads the report and results folders the Playwright config writes', () => {
      expect(upload).toMatch(/^\s*apps\/web\/playwright-report\/\s*$/m);
      expect(upload).toMatch(/^\s*apps\/web\/test-results\/\s*$/m);
      expect(config).toMatch(/outputFolder:\s*'playwright-report'/);
      expect(config).toMatch(/outputDir:\s*'test-results'/);
    });
  });
});

describe('playwright.config.ts (S-1.8.5)', () => {
  it('has a global timeout of 10 minutes or less', () => {
    const m = /globalTimeout:\s*(\d+)\s*\*\s*(\d+)(?:_(\d+))?/.exec(config);
    expect(m, 'globalTimeout is written as `<minutes> * 60_000`').not.toBeNull();
    const minutes = (Number(m?.[1]) * Number(`${m?.[2] ?? ''}${m?.[3] ?? ''}`)) / 60_000;
    expect(minutes).toBeLessThanOrEqual(10);
  });

  it('has exactly one project, chromium', () => {
    expect([...config.matchAll(/\bname:\s*'([^']+)'/g)].map((m) => m[1])).toEqual(['chromium']);
  });

  it('runs with one worker', () => {
    expect(config).toMatch(/\bworkers:\s*1\b/);
  });
});

describe('the e2e ESLint selector ban (S-1.8.5 AC4)', () => {
  const filePath = join(root, 'apps/web/e2e/lint-probe.ts');
  const eslint = new ESLint({ cwd: root, overrideConfigFile: join(root, 'eslint.config.js') });
  const banned = async (code: string): Promise<string[]> => {
    const [result] = await eslint.lintText(code, { filePath });
    return (result?.messages ?? [])
      .filter((m) => m.ruleId === 'no-restricted-syntax')
      .map((m) => m.message);
  };

  const rejected: [string, string][] = [
    ['page.locator', "page.locator('.a');"],
    ['page.$', "void page.$('.e');"],
    ['page.$$', "void page.$$('.e');"],
    ['page.$eval', "void page.$eval('.e', (el) => el);"],
    ['waitForSelector', "void page.waitForSelector('.e');"],
    ['computed locator access', "page['locator']('.a');"],
    ['computed $ access', "page['$']('.a');"],
    ['page.click with a selector', "void page.click('#go');"],
    ['page.fill with a selector', "void page.fill('.x', 'y');"],
    ['page.getAttribute with a selector', "void page.getAttribute('.x', 'y');"],
    ['document.querySelector', "document.querySelector('.a');"],
    ['querySelectorAll on an element', "el.querySelectorAll('.a');"],
    ['getElementById', "document.getElementById('a');"],
    ['frameLocator', "page.frameLocator('iframe');"],
  ];

  it.each(rejected)('rejects %s', { timeout: 90_000 }, async (_name, code) => {
    const messages = await banned(`declare const page: any;\ndeclare const el: any;\n${code}\n`);
    expect(messages.length).toBeGreaterThan(0);
    expect(messages[0]).toContain('S-1.8.5');
  });

  it('accepts role and label locators and keyboard presses', { timeout: 90_000 }, async () => {
    const code = [
      'declare const page: any;',
      "void page.getByRole('button').click();",
      "void page.getByLabel('Username').fill('x');",
      "void page.keyboard.press('Tab');",
      '',
    ].join('\n');
    expect(await banned(code)).toEqual([]);
  });

  it('does not apply outside apps/web/e2e', { timeout: 90_000 }, async () => {
    const [result] = await eslint.lintText('declare const page: any;\npage.locator(".a");\n', {
      filePath: join(root, 'apps/web/src/probe.ts'),
    });
    expect(result?.messages.filter((m) => m.ruleId === 'no-restricted-syntax')).toEqual([]);
  });
});
