// A valid API address for every test; tests of the missing/malformed case isolate the env module.
process.env.EXPO_PUBLIC_API_BASE_URL = 'http://localhost:8000/api/v1';

// Every QueryClient the app creates in tests (AppProviders, RootLayout) gets gcTime Infinity, so no
// 5-minute gc timer is scheduled when a query's observer unsubscribes on unmount (it would keep the
// Jest worker alive). Production code is unchanged.
jest.mock('@mtg-stats/api-client', () => {
  const actual =
    jest.requireActual<typeof import('@mtg-stats/api-client')>('@mtg-stats/api-client');
  return {
    ...actual,
    createQueryClient: (overrides: Parameters<typeof actual.createQueryClient>[0] = {}) =>
      actual.createQueryClient({
        ...overrides,
        defaultOptions: {
          ...overrides.defaultOptions,
          queries: { ...overrides.defaultOptions?.queries, gcTime: Infinity },
        },
      }),
  };
});

// Under Reanimated's Jest resolver Worklets runs its web build, whose getUIRuntimeHolder throws when
// Gesture Handler's deferred UI-runtime bootstrap calls it. There is no UI runtime in Jest.
jest.mock(
  'react-native-gesture-handler/lib/module/handlers/gestures/installUIRuntimeBindings',
  () => ({
    installUIRuntimeBindings: jest.fn(),
  }),
);
