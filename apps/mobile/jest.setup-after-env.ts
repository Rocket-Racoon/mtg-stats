/* eslint-disable @typescript-eslint/no-require-imports */
// The launch restore runs once per app process; every test is a fresh launch. The module is
// required lazily, inside the hook, so a test file's own jest.mock (for example of
// expo-secure-store) is registered before the session code is first loaded.
beforeEach(() => {
  const restore = require('./src/session/restore') as typeof import('./src/session/restore');
  restore.resetRestoreForTests();
});
