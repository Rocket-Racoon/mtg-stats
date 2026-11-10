import { Stack } from 'expo-router';

/**
 * Each tab is its own stack, so hardware back pops inside the tab before it leaves the app.
 * The root screen of the stack gets the tab's title instead of the route name.
 */
export function TabStack({ title }: { title: string }) {
  return (
    <Stack>
      <Stack.Screen name="index" options={{ title }} />
    </Stack>
  );
}
