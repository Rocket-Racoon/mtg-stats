import { useCallback, useEffect, useRef, useState } from 'react';
import { useTapGesture } from 'react-native-gesture-handler';

interface Geometry {
  /** Canvas x of each mark's centre. */
  xs: readonly number[];
  left: number;
  right: number;
}

/**
 * Tap selection for a chart, owned by React state.
 *
 * Victory's own press state is the wrong tool here: its pan only activates after a 100 ms long
 * press and clears on touch up, so a quick tap shows nothing and a held press shows the value only
 * while the finger is down. Instead a plain Tap gesture (which fails as soon as the finger moves,
 * so a parent ScrollView still scrolls) picks the mark whose centre is nearest to the tap:
 *  - a tap on a mark selects it, and the selection stays after release;
 *  - a tap on another mark moves the selection;
 *  - a tap on the selected mark clears it (so does `clear`, used when switching to the list).
 *
 * The chart reports its geometry from its render callback through `reportGeometry`.
 */
export function useTapSelection(count: number, onSelect?: (index: number | null) => void) {
  const [selected, setSelected] = useState<number | null>(null);
  const selectedRef = useRef<number | null>(null);
  const geometry = useRef<Geometry>({ xs: [], left: 0, right: 0 });
  const onSelectRef = useRef(onSelect);
  useEffect(() => {
    onSelectRef.current = onSelect;
  }, [onSelect]);

  const apply = useCallback((next: number | null) => {
    selectedRef.current = next;
    setSelected(next);
    onSelectRef.current?.(next);
  }, []);

  const handleTap = useCallback(
    (x: number) => {
      const { xs, left, right } = geometry.current;
      if (xs.length === 0 || x < left || x > right) return;
      let best = 0;
      xs.forEach((cx, i) => {
        if (Math.abs(cx - x) < Math.abs((xs[best] ?? 0) - x)) best = i;
      });
      apply(selectedRef.current === best ? null : best);
    },
    [apply],
  );

  const gesture = useTapGesture({
    testID: 'chart-tap',
    runOnJS: true,
    maxDistance: 12,
    onActivate: (e) => handleTap(e.x),
  });

  const reportGeometry = useCallback((xs: readonly number[], left: number, right: number) => {
    geometry.current = { xs, left, right };
  }, []);

  const clear = useCallback(() => {
    if (selectedRef.current !== null) apply(null);
  }, [apply]);

  return {
    /** The selected row index, or null (also null when the rows shrank below it). */
    selected: selected !== null && selected < count ? selected : null,
    gesture,
    reportGeometry,
    clear,
  };
}
