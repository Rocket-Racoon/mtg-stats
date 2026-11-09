module.exports = {
  preset: 'jest-expo',
  // testRegex, not testMatch: glob matching skips dot-directories, and worktrees live under .claude/.
  testRegex: String.raw`/src/.*\.test\.tsx?$`,
  // Reanimated 4 / Worklets 0.13 official Jest setup: swaps native-only modules for JS ones.
  // jest.setup.ts must come after the Gesture Handler setup.
  setupFiles: ['react-native-gesture-handler/jestSetup', '<rootDir>/jest.setup.ts'],
  resolver: require.resolve('react-native-reanimated/jest/resolver'),
};
