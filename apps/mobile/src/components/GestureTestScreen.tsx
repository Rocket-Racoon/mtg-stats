import { Pressable, StyleSheet, Text, View } from 'react-native';
import { GestureDetector, usePanGesture } from 'react-native-gesture-handler';
import Animated, { useAnimatedStyle, useSharedValue, withTiming } from 'react-native-reanimated';
import { scheduleOnRN } from 'react-native-worklets';

/*
 * Template for every drag interaction in the app: ONE commit path for drag and tap.
 *  - `target` is the logical position. `x`/`y` are only the animated view of it.
 *  - The drag follows the finger directly, and on release calls commitMove(next).
 *  - The tap alternative (Move button, accessibility action) calls the same commitMove(next),
 *    computed from `target`, so quick taps accumulate and never read the in-flight animated value.
 *  - commitMove stores the target, then animates x/y toward it with withTiming.
 */

export const MOVE_STEP = 40;

interface Point {
  x: number;
  y: number;
}

export function GestureTestScreen() {
  const x = useSharedValue(0);
  const y = useSharedValue(0);
  const target = useSharedValue<Point>({ x: 0, y: 0 });
  const dragStart = useSharedValue<Point>({ x: 0, y: 0 });

  const commitMove = (next: Point) => {
    target.set(next);
    x.set(withTiming(next.x));
    y.set(withTiming(next.y));
  };

  const moveByStep = () => {
    const current = target.get();
    commitMove({ x: current.x + MOVE_STEP, y: current.y + MOVE_STEP });
  };

  const pan = usePanGesture({
    testID: 'gesture-box-pan',
    onActivate: () => {
      'worklet';
      dragStart.set(target.get());
    },
    onUpdate: (e) => {
      'worklet';
      const start = dragStart.get();
      x.set(start.x + e.translationX);
      y.set(start.y + e.translationY);
    },
    onDeactivate: (e) => {
      'worklet';
      const start = dragStart.get();
      scheduleOnRN(commitMove, { x: start.x + e.translationX, y: start.y + e.translationY });
    },
  });

  const boxStyle = useAnimatedStyle(() => ({
    transform: [{ translateX: x.get() }, { translateY: y.get() }],
  }));

  return (
    <View style={styles.container}>
      <GestureDetector gesture={pan}>
        <Animated.View
          testID="gesture-box"
          accessible
          accessibilityLabel="Draggable box"
          accessibilityActions={[{ name: 'activate', label: 'Move' }]}
          onAccessibilityAction={(event) => {
            if (event.nativeEvent.actionName === 'activate') moveByStep();
          }}
          style={[styles.box, boxStyle]}
        />
      </GestureDetector>
      <Pressable
        testID="move-button"
        accessibilityRole="button"
        accessibilityLabel="Move box"
        onPress={moveByStep}
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
  button: {
    minHeight: 48,
    minWidth: 48,
    padding: 12,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderRadius: 6,
  },
});
