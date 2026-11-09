import { Pressable, StyleSheet, Text, View } from 'react-native';
import { GestureDetector, usePanGesture } from 'react-native-gesture-handler';
import Animated, {
  cancelAnimation,
  useAnimatedStyle,
  useSharedValue,
  withTiming,
} from 'react-native-reanimated';
import { scheduleOnUI } from 'react-native-worklets';

/*
 * Template for every drag interaction in the app: one commit path, owned by the UI thread.
 *  - `target` is the logical position and lives ONLY on the UI thread. `x`/`y` are the animated
 *    view of it. Nothing on the JS thread reads or writes `target`, so there is no stale cache.
 *  - `commitMove(next)` is a worklet: it sets `target` and animates `x`/`y` toward it.
 *  - The drag runs on the UI thread. It starts from the position currently shown (after
 *    cancelling any animation), follows the finger, and on release calls commitMove directly.
 *    If the system cancels the gesture (`e.canceled`) it snaps back to `target` and commits nothing.
 *  - The tap alternative (Move button, accessibility `activate` action) hops to the UI thread with
 *    scheduleOnUI, reads `target` there and calls the same commitMove. The UI thread runs these
 *    in FIFO order, so quick taps accumulate and never lose a step.
 *  - A tap during a drag is ignored on purpose (`isDragging`): the finger owns the box.
 *  - Use scheduleOnRN only for JS-side effects. None are needed here; persisting the position or
 *    calling the API would go there, after commitMove.
 *
 * The Jest tests cover this logic, not thread ordering. Check ordering on a device.
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
  const isDragging = useSharedValue(false);

  const commitMove = (next: Point) => {
    'worklet';
    target.set(next);
    x.set(withTiming(next.x));
    y.set(withTiming(next.y));
  };

  const moveByStep = () => {
    scheduleOnUI(() => {
      'worklet';
      if (isDragging.get()) return;
      const t = target.get();
      commitMove({ x: t.x + MOVE_STEP, y: t.y + MOVE_STEP });
    });
  };

  const pan = usePanGesture({
    testID: 'gesture-box-pan',
    onActivate: () => {
      'worklet';
      isDragging.set(true);
      cancelAnimation(x);
      cancelAnimation(y);
      dragStart.set({ x: x.get(), y: y.get() });
    },
    onUpdate: (e) => {
      'worklet';
      const start = dragStart.get();
      x.set(start.x + e.translationX);
      y.set(start.y + e.translationY);
    },
    onDeactivate: (e) => {
      'worklet';
      isDragging.set(false);
      if (e.canceled) {
        const t = target.get();
        x.set(withTiming(t.x));
        y.set(withTiming(t.y));
        return;
      }
      const start = dragStart.get();
      commitMove({ x: start.x + e.translationX, y: start.y + e.translationY });
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
