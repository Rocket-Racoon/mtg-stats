// Type-level pin for src/query-meta.d.ts: if the `Register` augmentation is lost, the expected
// compile error below disappears and `tsc -p tsconfig.tooling.json` fails.
import { useMutation } from '@mtg-stats/api-client';
import { describe, expect, it } from 'vitest';

type Options = Parameters<typeof useMutation>[0];

describe('mutation meta typing', () => {
  it('accepts errorToast: boolean and rejects other values at compile time', () => {
    const ok: Options = { mutationFn: () => Promise.resolve(), meta: { errorToast: false } };
    const bad: Options = {
      mutationFn: () => Promise.resolve(),
      // @ts-expect-error errorToast must be a boolean
      meta: { errorToast: 'no' },
    };
    expect(ok.meta).toEqual({ errorToast: false });
    expect(bad.meta).toBeDefined();
  });
});
