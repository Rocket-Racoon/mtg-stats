// A valid API address for every test; tests of the missing/malformed case isolate the env module.
process.env.EXPO_PUBLIC_API_BASE_URL = 'http://localhost:8000/api/v1';

// Under Reanimated's Jest resolver Worklets runs its web build, whose getUIRuntimeHolder throws when
// Gesture Handler's deferred UI-runtime bootstrap calls it. There is no UI runtime in Jest.
jest.mock(
  'react-native-gesture-handler/lib/module/handlers/gestures/installUIRuntimeBindings',
  () => ({
    installUIRuntimeBindings: jest.fn(),
  }),
);
