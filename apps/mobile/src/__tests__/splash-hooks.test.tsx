// S-1.7.3 AC1: the splash helpers hide the splash only when appropriate and never let a native
// failure crash the app; the real root layout keeps the splash up from import and the config error
// screen hides it.
import { renderHook } from '@testing-library/react-native';
import * as SplashScreen from 'expo-splash-screen';
import {
  preventSplashAutoHide,
  useHideSplashOnMount,
  useSplashWhileRestoring,
} from '../session/splash';
import type { SessionStatus } from '../session/store';

jest.mock('expo-splash-screen', () => ({
  preventAutoHideAsync: jest.fn(() => Promise.resolve(true)),
  hideAsync: jest.fn(() => Promise.resolve()),
}));

const prevent = jest.mocked(SplashScreen.preventAutoHideAsync);
const hide = jest.mocked(SplashScreen.hideAsync);

beforeEach(() => {
  prevent.mockReset().mockImplementation(() => Promise.resolve(true));
  hide.mockReset().mockImplementation(() => Promise.resolve());
});

describe('useSplashWhileRestoring', () => {
  it('does not hide the splash while restoring', async () => {
    await renderHook(() => useSplashWhileRestoring('restoring'));
    expect(hide).not.toHaveBeenCalled();
  });

  it.each(['signed-in', 'signed-out'] as SessionStatus[])(
    'hides the splash once for %s',
    async (status) => {
      await renderHook(() => useSplashWhileRestoring(status));
      expect(hide).toHaveBeenCalledTimes(1);
    },
  );

  it('hides the splash when the status changes from restoring to signed-in', async () => {
    const { rerender } = await renderHook(
      ({ status }: { status: SessionStatus }) => useSplashWhileRestoring(status),
      { initialProps: { status: 'restoring' as SessionStatus } },
    );
    expect(hide).not.toHaveBeenCalled();
    await rerender({ status: 'signed-in' });
    expect(hide).toHaveBeenCalledTimes(1);
  });

  it('swallows a hideAsync rejection', async () => {
    hide.mockImplementation(() => Promise.reject(new Error('no native splash')));
    await renderHook(() => useSplashWhileRestoring('signed-out'));
    await Promise.resolve();
    expect(hide).toHaveBeenCalledTimes(1);
  });

  it('swallows a synchronous hideAsync throw', async () => {
    hide.mockImplementation(() => {
      throw new Error('sync boom');
    });
    await expect(renderHook(() => useSplashWhileRestoring('signed-in'))).resolves.toBeDefined();
  });

  it('does not crash when hideAsync returns undefined (automock)', async () => {
    hide.mockImplementation(() => undefined as unknown as Promise<void>);
    await expect(renderHook(() => useSplashWhileRestoring('signed-in'))).resolves.toBeDefined();
  });
});

describe('preventSplashAutoHide', () => {
  it('calls preventAutoHideAsync', () => {
    preventSplashAutoHide();
    expect(prevent).toHaveBeenCalledTimes(1);
  });

  it('swallows a rejection', async () => {
    prevent.mockImplementation(() => Promise.reject(new Error('already hidden')));
    expect(() => preventSplashAutoHide()).not.toThrow();
    await Promise.resolve();
  });

  it('swallows a synchronous throw', () => {
    prevent.mockImplementation(() => {
      throw new Error('sync boom');
    });
    expect(() => preventSplashAutoHide()).not.toThrow();
  });

  it('does not crash when preventAutoHideAsync returns undefined (automock)', () => {
    prevent.mockImplementation(() => undefined as unknown as Promise<boolean>);
    expect(() => preventSplashAutoHide()).not.toThrow();
  });
});

describe('useHideSplashOnMount', () => {
  it('hides the splash on mount, once', async () => {
    await renderHook(() => useHideSplashOnMount());
    expect(hide).toHaveBeenCalledTimes(1);
  });

  it('swallows a rejection', async () => {
    hide.mockImplementation(() => Promise.reject(new Error('nope')));
    await renderHook(() => useHideSplashOnMount());
    await Promise.resolve();
    expect(hide).toHaveBeenCalledTimes(1);
  });
});

/* eslint-disable @typescript-eslint/no-require-imports */
describe('the root layout module', () => {
  beforeEach(() => {
    jest.resetModules();
  });

  it('calls preventAutoHideAsync exactly once at import, and not hideAsync', () => {
    const splash = require('expo-splash-screen') as typeof SplashScreen;
    require('../app/_layout');
    expect(jest.mocked(splash.preventAutoHideAsync)).toHaveBeenCalledTimes(1);
    expect(jest.mocked(splash.hideAsync)).not.toHaveBeenCalled();
  });
});
/* eslint-enable @typescript-eslint/no-require-imports */
