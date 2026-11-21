/* eslint-disable @typescript-eslint/no-require-imports */
// Shared Jest mocks for the chart kit: Skia and Victory need a native canvas, so they become
// plain Views that expose what the kit passed them. Import this file before the kit.
//
// The Victory mock keeps the two behaviors that matter to the kit and that a constant would hide:
//  - the x axis keeps only `tickCount` labels (default 5), thinned the way Victory's
//    downsampleTicks does it;
//  - there is no press state at all. Selection is the kit's own tap gesture, driven in tests
//    through chart-tap.ts (mark i sits at canvas x = i, plot area 0 to 100).
import type { ReactNode } from 'react';

jest.mock('@shopify/react-native-skia', () => {
  const { View } = require('react-native');
  const Stub = (testID: string) =>
    function SkiaStub(props: object) {
      return <View testID={testID} {...props} />;
    };
  return {
    matchFont: () => ({}),
    vec: (x: number, y: number) => ({ x, y }),
    Line: Stub('skia-line'),
    Rect: Stub('skia-rect'),
    Paint: Stub('skia-paint'),
    DashPathEffect: () => null,
  };
});

jest.mock('victory-native', () => {
  const { View, Text } = require('react-native');
  const point = (x: number, y: number | null) => ({ x, xValue: x, y, yValue: y });
  const thin = (ticks: number[], count = 5) =>
    ticks.length <= count
      ? ticks
      : count === 1
        ? [ticks[0]]
        : Array.from(
            { length: count },
            (_, i) => ticks[Math.round((i * (ticks.length - 1)) / (count - 1))],
          );
  const Part = (testID: string) =>
    function MockPart(props: {
      points: unknown;
      opacity?: number;
      radius?: number;
      style?: string;
    }) {
      return (
        <View
          testID={testID}
          count={Array.isArray(props.points) ? props.points.length : 0}
          opacity={props.opacity}
          radius={props.radius}
          paintStyle={props.style}
        />
      );
    };
  return {
    CartesianChart: (props: {
      data: Record<string, number | null>[];
      yKeys: string[];
      xAxis: {
        tickCount?: number;
        tickValues: number[];
        formatXLabel: (v: number) => string;
      };
      children: (a: unknown) => ReactNode;
    }) => {
      const points = Object.fromEntries(
        props.yKeys.map((k) => [k, props.data.map((d, i) => point(i, d[k] ?? null))]),
      );
      return (
        <View testID="victory-chart">
          {thin(props.xAxis.tickValues, props.xAxis.tickCount).map((t) => (
            <Text key={t} testID="x-label">
              {props.xAxis.formatXLabel(t as number)}
            </Text>
          ))}
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
