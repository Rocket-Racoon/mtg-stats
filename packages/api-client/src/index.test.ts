import { describe, expect, it } from 'vitest';
import { PACKAGE_NAME } from './index';

describe('@mtg-stats/api-client', () => {
  it('exports its placeholder package name', () => {
    expect(PACKAGE_NAME).toBe('@mtg-stats/api-client');
  });
});
