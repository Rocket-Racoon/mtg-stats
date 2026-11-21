import { useMemo } from 'react';
import { CartesianChart, Line, Scatter } from 'victory-native';
import { ChartFrame } from './ChartFrame';
import { axisLabel, defaultFormatValue, drawnValue, rowText, summarize } from './format';
import { useChartTheme } from './theme';
import type { ChartCommonProps, ChartRow } from './types';
import { useChartFont } from './useChartFont';
import { useTapSelection } from './useTapSelection';

export interface LineChartProps extends ChartCommonProps {
  /** Points in order, left to right. Values follow the rules on `ChartRow`. */
  rows: readonly ChartRow[];
  formatValue?: (value: number) => string;
}

/**
 * Line chart. Normal points are solid dots; small-sample points are small hollow rings, faded and
 * marked with "*" on the axis, so the difference is not only a color. Tapping a point shows its
 * value and "n games" and keeps it selected. A row with no value leaves a gap in the line.
 */
export function LineChart({
  title,
  rows,
  formatValue = defaultFormatValue,
  emptyMessage,
  height = 220,
  testID,
  onSelect,
}: LineChartProps) {
  const theme = useChartTheme();
  const font = useChartFont();
  const { selected, gesture, reportGeometry, clear } = useTapSelection(rows.length, onSelect);
  const data = useMemo(
    () => rows.map((r, index) => ({ index, value: drawnValue(r.value) })),
    [rows],
  );
  const ticks = useMemo(() => rows.map((_, i) => i), [rows]);
  const row = selected === null ? undefined : rows[selected];

  return (
    <ChartFrame
      title={title}
      testID={testID}
      height={height}
      emptyMessage={emptyMessage}
      isEmpty={!rows.some((r) => Number.isFinite(r.value))}
      summary={summarize(
        title,
        rows.map((r) => ({ label: r.label, value: r.value, small: r.low_sample })),
        formatValue,
      )}
      items={rows.map((r, i) => ({ key: `${i}-${r.label}`, text: rowText(r, formatValue) }))}
      tooltip={row ? rowText(row, formatValue) : null}
      hasLowSample={rows.some((r) => r.low_sample)}
      gesture={gesture}
      onShowList={clear}
    >
      <CartesianChart
        data={data}
        xKey="index"
        yKeys={['value']}
        domain={{ y: [0, Math.max(1, ...data.map((d) => d.value ?? 0))] }}
        domainPadding={{ left: 16, right: 16, top: 12, bottom: 4 }}
        xAxis={{
          font,
          // tickCount defaults to 5 and Victory thins the labels to that; keep every row's.
          tickCount: Math.max(rows.length, 1),
          tickValues: ticks,
          formatXLabel: (v) => {
            const r = rows[Number(v)];
            return r ? axisLabel(r.label, r.low_sample, rows.length) : '';
          },
          labelColor: theme.text,
          lineColor: theme.grid,
        }}
        yAxis={[
          {
            font,
            formatYLabel: (v) => formatValue(Number(v)),
            labelColor: theme.text,
            lineColor: theme.grid,
          },
        ]}
        frame={{ lineWidth: 0 }}
      >
        {({ points, chartBounds }) => {
          reportGeometry(
            points.value.map((p) => p.x),
            chartBounds.left,
            chartBounds.right,
          );
          const small = (i: number) => rows[i]?.low_sample === true;
          const withValue = (p: { y: number | null | undefined }) =>
            p.y !== null && p.y !== undefined;
          return (
            <>
              <Line
                points={points.value}
                color={theme.series[0]}
                strokeWidth={2}
                curveType="linear"
              />
              <Scatter
                points={points.value.filter((p, i) => withValue(p) && !small(i))}
                radius={5}
                color={theme.series[0]}
              />
              <Scatter
                points={points.value.filter((p, i) => withValue(p) && small(i))}
                radius={4}
                color={theme.series[0]}
                style="stroke"
                strokeWidth={2}
                opacity={0.8}
              />
            </>
          );
        }}
      </CartesianChart>
    </ChartFrame>
  );
}
