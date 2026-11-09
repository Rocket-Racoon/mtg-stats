/// <reference types="node" />
import path from 'node:path';

// The AC2 stand-in: Metro can only pick up a saved change in packages/game-core if it watches
// that folder and resolves hoisted node_modules. Real hot reload is manual (needs a device).
const repoRoot = path.resolve(__dirname, '../../../..');

describe('metro.config.cjs (AC2 stand-in)', () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const config = require('../../metro.config.cjs') as {
    watchFolders: string[];
    resolver: { nodeModulesPaths: string[] };
  };

  it('watches the packages/game-core workspace folder', () => {
    const watched = config.watchFolders.map((f) => path.resolve(f));
    expect(watched).toContain(path.join(repoRoot, 'packages', 'game-core'));
  });

  it('resolves modules from the repo root node_modules', () => {
    const paths = config.resolver.nodeModulesPaths.map((f) => path.resolve(f));
    expect(paths).toContain(path.join(repoRoot, 'node_modules'));
  });
});
