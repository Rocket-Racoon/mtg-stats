// S-2.4.5: the active-playgroup store: remembered per device, safe without storage, cleared on
// sign-out, reconciled against the caller's playgroups.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { clearLocalSession } from '../session/sign-out';
import {
  ACTIVE_PLAYGROUP_ITEM_NAME,
  reconcileActive,
  useActivePlaygroupStore,
} from './active-store';

const stored = () => localStorage.getItem(ACTIVE_PLAYGROUP_ITEM_NAME);

beforeEach(() => {
  localStorage.clear();
  useActivePlaygroupStore.setState({ activeId: null });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('persistence', () => {
  it('writes the choice to localStorage under one key', () => {
    useActivePlaygroupStore.getState().setActive('g1');
    expect(JSON.parse(stored() ?? 'null')).toMatchObject({ state: { activeId: 'g1' } });
  });

  it('remembers the choice across a restart (rehydrates from localStorage)', async () => {
    localStorage.setItem(
      ACTIVE_PLAYGROUP_ITEM_NAME,
      JSON.stringify({ state: { activeId: 'g7' }, version: 1 }),
    );
    await useActivePlaygroupStore.persist.rehydrate();
    expect(useActivePlaygroupStore.getState().activeId).toBe('g7');
  });

  it.each([
    ['not JSON', 'oops'],
    ['a number', JSON.stringify({ state: { activeId: 5 }, version: 1 })],
    ['an empty id', JSON.stringify({ state: { activeId: '' }, version: 1 })],
    ['no state', JSON.stringify({ version: 1 })],
  ])('ignores stored garbage (%s)', async (_name, raw) => {
    localStorage.setItem(ACTIVE_PLAYGROUP_ITEM_NAME, raw);
    await useActivePlaygroupStore.persist.rehydrate();
    expect(useActivePlaygroupStore.getState().activeId).toBeNull();
  });

  it('works when storage throws on read and on write', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('full');
    });
    await useActivePlaygroupStore.persist.rehydrate();
    expect(() => {
      useActivePlaygroupStore.getState().setActive('g2');
    }).not.toThrow();
    expect(useActivePlaygroupStore.getState().activeId).toBe('g2');
  });
});

describe('sign-out', () => {
  it('clears the choice and the stored value, so the next account does not inherit it', () => {
    useActivePlaygroupStore.getState().setActive('g1');
    expect(stored()).not.toBeNull();
    clearLocalSession('user');
    expect(useActivePlaygroupStore.getState().activeId).toBeNull();
    expect(stored()).toBeNull();
  });

  it('also clears it when the session expires', () => {
    useActivePlaygroupStore.getState().setActive('g1');
    clearLocalSession('expired');
    expect(useActivePlaygroupStore.getState().activeId).toBeNull();
    expect(stored()).toBeNull();
  });
});

describe('reconcileActive', () => {
  const groups = [{ id: 'a' }, { id: 'b' }, { id: 'c' }];

  it('keeps the stored id while it is one of the playgroups', () => {
    expect(reconcileActive('b', groups)).toBe('b');
  });

  it('falls back to the first playgroup in the API order when the id is gone', () => {
    expect(reconcileActive('gone', groups)).toBe('a');
  });

  it('falls back to the first playgroup when nothing is remembered', () => {
    expect(reconcileActive(null, groups)).toBe('a');
  });

  it('has no active playgroup when there are none', () => {
    expect(reconcileActive('b', [])).toBeNull();
    expect(reconcileActive(null, [])).toBeNull();
  });
});
