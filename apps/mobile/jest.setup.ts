// Under Reanimated's Jest resolver Worklets runs its web build, whose getUIRuntimeHolder throws when
// Gesture Handler's deferred UI-runtime bootstrap calls it. There is no UI runtime in Jest.
jest.mock(
  'react-native-gesture-handler/lib/module/handlers/gestures/installUIRuntimeBindings',
  () => ({
    installUIRuntimeBindings: jest.fn(),
  }),
);
