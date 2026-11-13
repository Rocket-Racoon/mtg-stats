import Constants from 'expo-constants';

/** The build profile from `app.config.ts` (`extra.buildProfile`); "development" if absent. */
export function buildProfile(): string {
  const profile: unknown = Constants.expoConfig?.extra?.buildProfile;
  return typeof profile === 'string' && profile ? profile : 'development';
}

/**
 * A "dev build" is a bundle with `__DEV__` true (Metro dev server, Expo Go or a dev client) whose
 * profile is not `production`. `__DEV__` is false in every release bundle whatever the profile,
 * so a release build can never show the API URL even if a profile is misconfigured.
 */
export function isDevBuild(): boolean {
  return __DEV__ && buildProfile() !== 'production';
}
