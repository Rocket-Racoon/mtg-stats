import { describe, expect, it } from 'vitest';
import { PACKAGE_NAME } from './index';

describe('@mtg-stats/card-utils', () => {
  it('exports its placeholder package name', () => {
    expect(PACKAGE_NAME).toBe('@mtg-stats/card-utils');
  });
});
