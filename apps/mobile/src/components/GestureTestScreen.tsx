import { Pressable, StyleSheet, Text, View } from 'react-native';
import { Gesture, GestureDetector } from 'react-native-gesture-handler';
import Animated, { useAnimatedStyle, useSharedValue, withTiming } from 'react-native-reanimated';

export const MOVE_STEP = 40;

/**
 * Reference pattern for every drag interaction: a Pan gesture plus a tap alternative
 * (the "Move" button) that produces the same movement.
 */
export function GestureTestScreen() {
  const x = useSharedValue(0);
  const y = useSharedValue(0);
  const startX = useSharedValue(0);
  const startY = useSharedValue(0);

  const pan = Gesture.Pan()
    .onStart(() => {
      startX.set(x.get());
      startY.set(y.get());
    })
    .onUpdate((e) => {
      x.set(startX.get() + e.translationX);
      y.set(startY.get() + e.translationY);
    });

  const boxStyle = useAnimatedStyle(() => ({
    transform: [{ translateX: x.get() }, { translateY: y.get() }],
  }));

  const move = () => {
    x.set(withTiming(x.get() + MOVE_STEP));
    y.set(withTiming(y.get() + MOVE_STEP));
  };

  return (
    <View style={styles.container}>
      <GestureDetector gesture={pan}>
        <Animated.View
          testID="gesture-box"
          accessibilityLabel="Draggable box"
          style={[styles.box, boxStyle]}
        />
      </GestureDetector>
      <Pressable
        testID="move-button"
        accessibilityRole="button"
        accessibilityLabel="Move box"
        onPress={move}
        style={styles.button}
      >
        <Text>Move</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 32 },
  box: { width: 80, height: 80, backgroundColor: '#4f46e5', borderRadius: 8 },
  button: { padding: 12, borderWidth: 1, borderRadius: 6 },
});
