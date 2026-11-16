const fs = require('node:fs');
const path = require('node:path');

// The app's own react (19.3.0) and the single TanStack copy that @mtg-stats/api-client resolves.
const reactDir = fs.realpathSync(path.join(__dirname, 'node_modules/react'));
const apiClientDir = fs.realpathSync(path.join(__dirname, 'node_modules/@mtg-stats/api-client'));
const tanstackDir = fs.realpathSync(path.join(apiClientDir, 'node_modules/@tanstack/react-query'));

module.exports = {
  preset: 'jest-expo',
  // testRegex, not testMatch: glob matching skips dot-directories, and worktrees live under .claude/.
  testRegex: String.raw`/src/.*\.test\.tsx?$`,
  // Reanimated 4 / Worklets 0.13 official Jest setup: swaps native-only modules for JS ones.
  // jest.setup.ts must come after the Gesture Handler setup.
  setupFilesAfterEnv: ['<rootDir>/jest.setup-after-env.ts'],
  setupFiles: ['react-native-gesture-handler/jestSetup', '<rootDir>/jest.setup.ts'],
  resolver: require.resolve('react-native-reanimated/jest/resolver'),
  // jest-expo has no Metro resolver, so api-client would load its own react@19.3.1 and fail with
  // "Invalid hook call". Pin react (and its subpaths) and TanStack to one copy each.
  moduleNameMapper: {
    '^react$': reactDir,
    '^react/(.*)$': `${reactDir}/$1`,
    '^@tanstack/react-query$': tanstackDir,
  },
};
