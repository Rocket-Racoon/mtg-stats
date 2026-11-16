import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, beforeEach, vi } from 'vitest';

// Unit tests never reach the network. Every test starts with a fetch that rejects; a test that
// needs a response stubs its own with vi.stubGlobal('fetch', ...), which replaces this one.
beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      return Promise.reject(new TypeError(`Unstubbed fetch in a unit test: ${url}`));
    }),
  );
});

afterEach(() => {
  cleanup();
});
