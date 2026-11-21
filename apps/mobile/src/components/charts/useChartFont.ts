import { matchFont } from '@shopify/react-native-skia';
import { Platform } from 'react-native';
import { useMemo } from 'react';

/** A system font for axis labels, so the kit ships no font asset. */
export function useChartFont(size = 12) {
  return useMemo(
    () =>
      matchFont({
        fontFamily: Platform.select({ android: 'sans-serif', default: 'Helvetica' }),
        fontSize: size,
      }),
    [size],
  );
}
