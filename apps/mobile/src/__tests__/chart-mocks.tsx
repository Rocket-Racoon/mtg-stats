/* eslint-disable @typescript-eslint/no-require-imports */
// Shared Jest mocks for the chart kit: Skia and Victory need a native canvas, so they become
// plain Views that expose what the kit passed them. Import this file before the kit.
import type { ReactNode } from 'react';

export const mockPressed = { index: -1 };

jest.mock('@shopify/react-native-skia', () => {
  const { View } = require('react-native');
  return {
    matchFont: () => ({}),
    vec: (x: number, y: number) => ({ x, y }),
    Line: (props: object) => <View testID="skia-line" {...props} />,
    DashPathEffect: () => null,
  };
});

jest.mock('react-native-reanimated', () => {
  const actual = jest.requireActual('react-native-reanimated');
  const { useEffect } = require('react');
  return {
    ...actual,
    // Runs the reaction once with the test pressed state, as the UI thread would on a press.
    useAnimatedReaction: (prepare: () => number, react: (v: number, p: number | null) => void) => {
      useEffect(() => {
        react(prepare(), null);
        // eslint-disable-next-line react-hooks/exhaustive-deps
      }, []);
    },
  };
});

jest.mock('victory-native', () => {
  const { View } = require('react-native');
  const point = (x: number, y: number) => ({ x, xValue: x, y, yValue: y });
  const Part = (testID: string) =>
    function MockPart(props: { points: unknown; opacity?: number; radius?: number }) {
      return (
        <View
          testID={testID}
          count={Array.isArray(props.points) ? props.points.length : 0}
          opacity={props.opacity}
          radius={props.radius}
        />
      );
    };
  return {
    useChartPressState: (init: { y: Record<string, number> }) => ({
      state: {
        isActive: { get: () => mockPressed.index >= 0 },
        matchedIndex: { get: () => mockPressed.index },
        x: {},
        y: Object.fromEntries(Object.keys(init.y).map((k) => [k, {}])),
      },
      isActive: false,
    }),
    CartesianChart: (props: {
      data: Record<string, number>[];
      yKeys: string[];
      children: (a: unknown) => ReactNode;
    }) => {
      const points = Object.fromEntries(
        props.yKeys.map((k) => [k, props.data.map((d, i) => point(i, d[k] ?? 0))]),
      );
      return (
        <View testID="victory-chart">
          {props.children({
            points,
            chartBounds: { left: 0, right: 100, top: 0, bottom: 100 },
            yScale: (v: number) => 100 - v,
          })}
        </View>
      );
    },
    Bar: Part('victory-bar'),
    Line: Part('victory-line'),
    Scatter: Part('victory-scatter'),
    StackedBar: Part('victory-stacked-bar'),
  };
});
