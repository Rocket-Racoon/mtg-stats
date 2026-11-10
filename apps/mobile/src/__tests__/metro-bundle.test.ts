import { spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

// S-1.5.2 AC4: Metro bundles the app, including @mtg-stats/api-client, without Node-only APIs.
const mobileRoot = path.resolve(__dirname, '../..');

describe('metro bundle (slow, S-1.5.2 AC4)', () => {
  it('SLOW: expo export for android succeeds and the bundle contains the api-client', () => {
    const outDir = mkdtempSync(path.join(tmpdir(), 'mobile-export-'));
    try {
      const expoCli = require.resolve('expo/bin/cli', { paths: [mobileRoot] });
      const result = spawnSync(
        process.execPath,
        [expoCli, 'export', '--platform', 'android', '--output-dir', outDir],
        { cwd: mobileRoot, encoding: 'utf8', env: { ...process.env, CI: '1' } },
      );
      if (result.status !== 0) {
        throw new Error(
          `expo export failed (${result.status}):\n${result.stdout}\n${result.stderr}`,
        );
      }

      const jsDir = path.join(outDir, '_expo', 'static', 'js', 'android');
      expect(existsSync(jsDir)).toBe(true);
      const bundles = readdirSync(jsDir).map((f) =>
        readFileSync(path.join(jsDir, f)).toString('latin1'),
      );
      expect(bundles.length).toBeGreaterThan(0);
      expect(bundles.join('\n')).toContain('unexpected_response');
    } finally {
      rmSync(outDir, { recursive: true, force: true });
    }
  }, 300_000);
});
