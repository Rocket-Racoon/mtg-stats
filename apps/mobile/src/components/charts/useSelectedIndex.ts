import { useState } from 'react';
import { useAnimatedReaction } from 'react-native-reanimated';
import { scheduleOnRN } from 'react-native-worklets';

interface PressState {
  isActive: { get(): boolean };
  matchedIndex: { get(): number };
}

/** Mirrors Victory's UI-thread press state into React state: the pressed row index, or null. */
export function useSelectedIndex(state: PressState): number | null {
  const [selected, setSelected] = useState<number | null>(null);
  useAnimatedReaction(
    () => (state.isActive.get() ? state.matchedIndex.get() : -1),
    (index, previous) => {
      if (index !== previous) scheduleOnRN(setSelected, index >= 0 ? index : null);
    },
  );
  return selected;
}
