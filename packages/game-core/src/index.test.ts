import { describe, expect, it } from 'vitest';
import { PACKAGE_NAME } from './index';

describe('@mtg-stats/game-core', () => {
  it('exports its placeholder package name', () => {
    expect(PACKAGE_NAME).toBe('@mtg-stats/game-core');
  });
});
