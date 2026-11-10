// Expo SDK 52+ auto-configures watchFolders and node_modules resolution for monorepos,
// so the default config already watches packages/* at the workspace root.
const { getDefaultConfig } = require('expo/metro-config');

module.exports = getDefaultConfig(__dirname);
