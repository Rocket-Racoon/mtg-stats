import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, beforeEach, vi } from 'vitest';
import { ACTIVE_PLAYGROUP_ITEM_NAME, useActivePlaygroupStore } from '../playgroups/active-store';

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
  // The remembered playgroup (S-2.4.5) lives in localStorage and a store: never leak it between tests.
  useActivePlaygroupStore.setState({ activeId: null });
  try {
    localStorage.removeItem(ACTIVE_PLAYGROUP_ITEM_NAME);
  } catch {
    // A test that broke localStorage on purpose.
  }
});

// jsdom has no <dialog>.showModal()/close(): a minimal stand-in that toggles `open` (the real
// top layer and inert background are covered by the Playwright e2e suite).
if (typeof HTMLDialogElement !== 'undefined') {
  const proto = HTMLDialogElement.prototype as Partial<HTMLDialogElement>;
  proto.showModal ??= function (this: HTMLDialogElement) {
    this.setAttribute('open', '');
  };
  proto.close ??= function (this: HTMLDialogElement) {
    this.removeAttribute('open');
  };
}
