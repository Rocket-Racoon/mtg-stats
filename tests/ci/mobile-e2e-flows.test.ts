/**
 * S-1.8.6 repo-side guards for the Maestro flows in apps/mobile/e2e/. The flow is read as text (no
 * YAML library at the root); Maestro itself proves the flow on an emulator in CI.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const dir = join(import.meta.dirname, '..', '..', 'apps/mobile/e2e');
const read = (p: string) => readFileSync(join(dir, p), 'utf8').replace(/\r\n/g, '\n');
const smoke = read('smoke.yaml');
/** The flow without full-line comments, so prose in comments cannot satisfy a guard. */
const code = smoke
  .split('\n')
  .filter((l) => !l.trim().startsWith('#'))
  .join('\n');

/** Index of the first match of `re` in `code` at or after `from`; -1 when there is none. */
const find = (re: RegExp, from = 0): number => {
  const i = code.slice(from).search(re);
  return i === -1 ? -1 : from + i;
};

describe('Maestro smoke flow (S-1.8.6)', () => {
  it('targets the app id app.mtgstats', () => {
    expect(smoke).toMatch(/^appId:\s*app\.mtgstats\s*$/m);
  });

  it('starts from a clean app: clearState on launchApp', () => {
    expect(code).toMatch(/- launchApp:\s*\n\s+clearState:\s*true/);
  });

  it('reads credentials only from the MAESTRO_E2E_* variables', () => {
    const inputs = [...code.matchAll(/^\s*- inputText:\s*(.*)$/gm)].map((m) => m[1]?.trim());
    expect(inputs).toContain('${MAESTRO_E2E_USERNAME}');
    expect(inputs).toContain('${MAESTRO_E2E_PASSWORD}');
    for (const v of inputs) expect(v).toMatch(/^\$\{MAESTRO_E2E_(USERNAME|PASSWORD)\}$/);
  });

  it('does not use coordinate taps', () => {
    expect(code).not.toMatch(/\bpoint:/);
  });

  it('bounds the network waits with a timeout', () => {
    const waits = [...code.matchAll(/extendedWaitUntil:[\s\S]*?timeout:\s*(\d+)/g)];
    expect(waits.length).toBeGreaterThanOrEqual(3);
    for (const w of waits) expect(Number(w[1])).toBeLessThanOrEqual(120_000);
  });

  it('does not use Sign out everywhere (it opens a native Alert)', () => {
    expect(code).not.toMatch(/sign-out-everywhere/);
  });

  it('runs the journey in acceptance-criteria order', () => {
    const steps: [string, RegExp][] = [
      ['sign in: username', /id:\s*sign-in-username/],
      ['sign in: password', /id:\s*sign-in-password/],
      ['sign in: submit', /id:\s*sign-in-submit/],
      ['tab-play', /id:\s*tab-play/],
      ['tab-decks', /id:\s*tab-decks/],
      ['tab-stats', /id:\s*tab-stats/],
      ['tab-more', /id:\s*tab-more/],
      ['more-about', /id:\s*more-about/],
      ['about-screen', /id:\s*about-screen/],
      ['openLink to a join link', /openLink:\s*['"]?mtgstats:\/\/join\/\S+/],
      ['join-screen', /id:\s*join-screen\b(?!-)/],
      ['sign-out', /id:\s*sign-out\s*$/m],
      ['sign-in-submit again', /id:\s*sign-in-submit/],
    ];
    let at = 0;
    for (const [name, re] of steps) {
      const i = find(re, at);
      expect(i, `"${name}" appears after the previous step`).toBeGreaterThanOrEqual(0);
      at = i + 1;
    }
  });

  it('taps all four tab buttons and asserts each tab screen root', () => {
    for (const t of ['play', 'decks', 'stats', 'more']) {
      expect(code, `tap tab-${t}`).toMatch(new RegExp(`- tapOn:\\s*\\n\\s+id:\\s*tab-${t}\\s*\\n`));
      expect(code, `${t}-screen`).toMatch(new RegExp(`id:\\s*${t}-screen\\b`));
    }
  });

  it('opens About and asserts both notices', () => {
    expect(code).toMatch(/id:\s*fan-content-notice/);
    expect(code).toMatch(/id:\s*scryfall-credit/);
  });

  it('opens the join link SMOKE42 and sees the code', () => {
    expect(code).toMatch(/openLink:\s*['"]?mtgstats:\/\/join\/SMOKE42['"]?\s*$/m);
    expect(code).toMatch(/SMOKE42\.\*/);
  });

  it('ends on the sign-in screen after signing out', () => {
    const lastSignOut = code.lastIndexOf('id: sign-out');
    expect(lastSignOut).toBeGreaterThan(-1);
    expect(code.slice(lastSignOut)).toMatch(/id:\s*sign-in-submit/);
  });

  it('has no other top-level flow that maestro would also run, and no hard-coded password', () => {
    const topLevel = readdirSync(dir).filter((f) => /\.ya?ml$/.test(f));
    expect(topLevel).toContain('smoke.yaml');
    for (const f of topLevel) {
      expect(read(f), f).not.toMatch(/password:\s*\S|inputText:\s*(?!\$\{MAESTRO_E2E_)\S{8,}/i);
    }
  });
});
