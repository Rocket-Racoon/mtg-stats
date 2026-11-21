import { useMemo } from 'react';
import { CartesianChart, Line, Scatter, useChartPressState } from 'victory-native';
import { ChartFrame } from './ChartFrame';
import { defaultFormatValue, sampleText, summarize } from './format';
import { useChartTheme } from './theme';
import type { ChartCommonProps, ChartRow } from './types';
import { useChartFont } from './useChartFont';
import { useSelectedIndex } from './useSelectedIndex';

export interface LineChartProps extends ChartCommonProps {
  /** Points in order, left to right. */
  rows: readonly ChartRow[];
  formatValue?: (value: number) => string;
}

function rowText(row: ChartRow, formatValue: (v: number) => string): string {
  return [`${row.label}: ${formatValue(row.value)}`, sampleText(row)].filter(Boolean).join(', ');
}

/**
 * Line chart. Normal points are solid dots; low-sample points are small, faded and marked with
 * "*" on the axis, so the difference is not only a color.
 */
export function LineChart({
  title,
  rows,
  formatValue = defaultFormatValue,
  emptyMessage,
  height = 220,
  testID,
}: LineChartProps) {
  const theme = useChartTheme();
  const font = useChartFont();
  const { state } = useChartPressState({ x: 0, y: { value: 0 } });
  const selected = useSelectedIndex(state);
  const data = useMemo(() => rows.map((r, index) => ({ index, value: r.value })), [rows]);
  const ticks = useMemo(() => rows.map((_, i) => i), [rows]);
  const row = selected === null ? undefined : rows[selected];

  return (
    <ChartFrame
      title={title}
      testID={testID}
      height={height}
      emptyMessage={emptyMessage}
      isEmpty={rows.length === 0}
      summary={summarize(title, rows, formatValue)}
      items={rows.map((r, i) => ({ key: `${i}-${r.label}`, text: rowText(r, formatValue) }))}
      tooltip={row ? rowText(row, formatValue) : null}
      hasLowSample={rows.some((r) => r.low_sample)}
    >
      <CartesianChart
        data={data}
        xKey="index"
        yKeys={['value']}
        domainPadding={{ left: 16, right: 16, top: 12, bottom: 4 }}
        chartPressState={state}
        xAxis={{
          font,
          tickValues: ticks,
          formatXLabel: (v) => {
            const r = rows[Number(v)];
            return r ? `${r.label}${r.low_sample ? '*' : ''}` : '';
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
        {({ points }) => (
          <>
            <Line
              points={points.value}
              color={theme.series[0]}
              strokeWidth={2}
              curveType="linear"
            />
            <Scatter
              points={points.value.filter((_, i) => !rows[i]?.low_sample)}
              radius={5}
              color={theme.series[0]}
            />
            <Scatter
              points={points.value.filter((_, i) => rows[i]?.low_sample)}
              radius={3}
              color={theme.series[0]}
              opacity={0.4}
            />
          </>
        )}
      </CartesianChart>
    </ChartFrame>
  );
}
