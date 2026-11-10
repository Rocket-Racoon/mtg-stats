// S-1.6.5 AC1: public/theme-init.js (loaded by a blocking <script src> in index.html) is a
// second copy of the theme resolution in src/theme/store.ts. These tests run the real file and
// the real store on the same inputs so the two cannot drift.
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(resolve(here, '../index.html'), 'utf8');
const bootScript = readFileSync(resolve(here, '../public/theme-init.js'), 'utf8');

type Stored = 'light' | 'dark' | 'garbage' | 'throws' | null;
type System = 'dark' | 'light' | 'no-matchmedia';

function setup(stored: Stored, system: System, key: string): void {
  localStorage.clear();
  document.documentElement.removeAttribute('data-theme');
  if (stored === 'throws') {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
  } else if (stored !== null) {
    localStorage.setItem(key, stored);
  }
  if (system === 'no-matchmedia') {
    vi.stubGlobal('matchMedia', undefined);
  } else {
    vi.stubGlobal(
      'matchMedia',
      vi.fn((q: string) => ({
        matches: q.includes('prefers-color-scheme: dark') && system === 'dark',
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    );
  }
}

function runBootScript(): string | undefined {
  new Function(bootScript)();
  return document.documentElement.dataset.theme;
}

async function runStore(): Promise<string | undefined> {
  vi.resetModules();
  document.documentElement.removeAttribute('data-theme');
  const store = await import('../src/theme/store');
  const stop = store.startThemeSync();
  const result = document.documentElement.dataset.theme;
  stop();
  return result;
}

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute('data-theme');
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  document.documentElement.removeAttribute('data-theme');
});

describe('index.html loads the pre-render theme script (S-1.6.5 AC1)', () => {
  const tags = [...html.matchAll(/<script\b([^>]*)>/g)].map((m) => m[1] ?? '');

  it('has no inline script without src (CSP guard, S-11.6.1)', () => {
    expect(tags.length).toBeGreaterThan(0);
    for (const attrs of tags) expect(attrs).toMatch(/\bsrc\s*=/);
    expect(html).not.toMatch(/<script(?![^>]*\bsrc\s*=)[^>]*>\s*\S/);
  });

  it('loads /theme-init.js in <head> before the module entry, blocking', () => {
    const head = html.slice(html.indexOf('<head>'), html.indexOf('</head>'));
    const tag = /<script\b([^>]*\bsrc\s*=\s*"\/theme-init\.js"[^>]*)>/.exec(head);
    expect(tag).not.toBeNull();
    const attrs = tag?.[1] ?? '';
    expect(attrs).not.toMatch(/\basync\b|\bdefer\b|type\s*=\s*"module"/);
    expect(html.indexOf('/theme-init.js')).toBeLessThan(html.indexOf('/src/main.tsx'));
  });

  it('warns on its first line that it must not be served immutable', () => {
    const first = bootScript.split(/\r?\n/)[0] ?? '';
    expect(first).toMatch(/never.*immutable/i);
  });

  it('reads the same storage key the store writes', async () => {
    const { THEME_ITEM_NAME } = await import('../src/theme/store');
    const keys = [...bootScript.matchAll(/getItem\(\s*'([^']+)'\s*\)/g)].map((m) => m[1]);
    expect(keys).toEqual([THEME_ITEM_NAME]);
  });

  it('sets dark on first load when the system is dark and nothing is stored', () => {
    setup(null, 'dark', 'mtg-stats-theme');
    expect(runBootScript()).toBe('dark');
  });

  it('sets light when the system is light and nothing is stored', () => {
    setup(null, 'light', 'mtg-stats-theme');
    expect(runBootScript()).toBe('light');
  });

  it('a stored light choice overrides a dark system', () => {
    setup('light', 'dark', 'mtg-stats-theme');
    expect(runBootScript()).toBe('light');
  });

  it('a stored dark choice overrides a light system', () => {
    setup('dark', 'light', 'mtg-stats-theme');
    expect(runBootScript()).toBe('dark');
  });

  it('falls back to the system theme when storage throws', () => {
    setup('throws', 'dark', 'mtg-stats-theme');
    expect(runBootScript()).toBe('dark');
  });

  it('ignores an invalid stored value', () => {
    setup('garbage', 'dark', 'mtg-stats-theme');
    expect(runBootScript()).toBe('dark');
  });

  it('does not throw and uses light when matchMedia is unavailable', () => {
    setup(null, 'no-matchmedia', 'mtg-stats-theme');
    expect(runBootScript()).toBe('light');
  });
});

describe('theme-init.js and theme store agree (S-1.6.5 AC1)', () => {
  const storedCases: Stored[] = [null, 'light', 'dark', 'garbage', 'throws'];
  const systemCases: System[] = ['dark', 'light', 'no-matchmedia'];
  for (const stored of storedCases) {
    for (const system of systemCases) {
      it(`stored=${String(stored)} system=${system}`, async () => {
        const { THEME_ITEM_NAME } = await import('../src/theme/store');
        setup(stored, system, THEME_ITEM_NAME);
        const fromScript = runBootScript();
        const fromStore = await runStore();
        expect(fromScript).toBeDefined();
        expect(fromStore).toBe(fromScript);
      });
    }
  }
});

describe('remembered on this device (S-1.6.5 AC1)', () => {
  it('a fresh store instance starts in the chosen theme, against the system', async () => {
    setup(null, 'dark', 'mtg-stats-theme');
    vi.resetModules();
    const first = await import('../src/theme/store');
    first.useThemeStore.getState().setPreference('light');

    expect(await runStore()).toBe('light');
    // The pre-render script on the next page load agrees.
    document.documentElement.removeAttribute('data-theme');
    expect(runBootScript()).toBe('light');
  });

  it('a later switch back to dark is remembered as well', async () => {
    setup(null, 'light', 'mtg-stats-theme');
    vi.resetModules();
    const first = await import('../src/theme/store');
    first.useThemeStore.getState().setPreference('dark');
    expect(await runStore()).toBe('dark');
  });
});
