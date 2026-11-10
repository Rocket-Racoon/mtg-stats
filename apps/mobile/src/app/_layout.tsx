import { Stack } from 'expo-router';
import { GestureHandlerRootView } from 'react-native-gesture-handler';

export default function RootLayout() {
  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <Stack>
        {/* Dev-only route: not registered in production builds. */}
        <Stack.Protected guard={__DEV__}>
          <Stack.Screen name="dev/gesture-test" />
        </Stack.Protected>
      </Stack>
    </GestureHandlerRootView>
  );
}
