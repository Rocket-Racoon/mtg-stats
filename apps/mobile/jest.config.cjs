module.exports = {
  preset: 'jest-expo',
  // testRegex, not testMatch: glob matching skips dot-directories, and worktrees live under .claude/.
  testRegex: String.raw`/src/.*\.test\.tsx?$`,
};
