import { useMemo } from 'react';
import { CartesianChart, StackedBar, useChartPressState } from 'victory-native';
import { ChartFrame } from './ChartFrame';
import { sampleText, summarize } from './format';
import { useChartTheme } from './theme';
import type { ChartCommonProps, StackedRow, StackedSeries } from './types';
import { useChartFont } from './useChartFont';
import { useSelectedIndex } from './useSelectedIndex';

export interface StackedBarChartProps extends ChartCommonProps {
  /** The segments, bottom to top. Every row's `segments` is keyed by `series[].key`. */
  series: readonly StackedSeries[];
  rows: readonly StackedRow[];
}

type Shares = Record<string, number>;

/** Each row's segments as percentages of the row total (all zero when the total is zero). */
export function toShares(row: StackedRow, series: readonly StackedSeries[]): Shares {
  const total = series.reduce((sum, s) => sum + Math.max(row.segments[s.key] ?? 0, 0), 0);
  const out: Shares = {};
  for (const s of series) {
    out[s.key] = total > 0 ? (Math.max(row.segments[s.key] ?? 0, 0) / total) * 100 : 0;
  }
  return out;
}

const pct = (v: number) => `${Math.round(v)}%`;

function rowText(row: StackedRow, series: readonly StackedSeries[], shares: Shares): string {
  const parts = series.map((s) => `${s.label} ${pct(shares[s.key] ?? 0)}`).join(', ');
  return [`${row.label}: ${parts}`, sampleText(row)].filter(Boolean).join(', ');
}

/**
 * 100% stacked bar chart. Every bar fills the axis and each segment is its share of the row. The
 * legend, the tooltip and the list view name each segment with its percentage, so color is not
 * the only way to tell segments apart. Low-sample bars are faded and marked with "*".
 */
export function StackedBarChart({
  title,
  series,
  rows,
  emptyMessage,
  height = 220,
  testID,
}: StackedBarChartProps) {
  const theme = useChartTheme();
  const font = useChartFont();
  const keys = useMemo(() => series.map((s) => s.key), [series]);
  const init = useMemo(() => ({ x: 0, y: Object.fromEntries(keys.map((k) => [k, 0])) }), [keys]);
  const { state } = useChartPressState(init);
  const selected = useSelectedIndex(state);
  const shares = useMemo(() => rows.map((r) => toShares(r, series)), [rows, series]);
  const data = useMemo(
    () => rows.map((_, index) => ({ index, ...shares[index] })) as Record<string, number>[],
    [rows, shares],
  );
  const ticks = useMemo(() => rows.map((_, i) => i), [rows]);
  const colors = series.map((_, i) => theme.series[i % theme.series.length] ?? theme.text);

  // Top values for the summary: the largest segment shares across all rows.
  const entries = rows.flatMap((r, i) =>
    series.map((s) => ({ label: `${r.label} ${s.label}`, value: shares[i]?.[s.key] ?? 0 })),
  );
  const selectedRow = selected === null ? undefined : rows[selected];
  const selectedShares = selected === null ? undefined : shares[selected];

  return (
    <ChartFrame
      title={title}
      testID={testID}
      height={height}
      emptyMessage={emptyMessage}
      isEmpty={rows.length === 0 || series.length === 0}
      summary={summarize(title, entries, pct)}
      items={rows.map((r, i) => ({
        key: `${i}-${r.label}`,
        text: rowText(r, series, shares[i] ?? {}),
      }))}
      tooltip={selectedRow && selectedShares ? rowText(selectedRow, series, selectedShares) : null}
      hasLowSample={rows.some((r) => r.low_sample)}
      legend={[`Segments, bottom to top: ${series.map((s) => s.label).join(', ')}`]}
    >
      <CartesianChart
        data={data}
        xKey="index"
        yKeys={keys}
        domain={{ y: [0, 100] }}
        domainPadding={{ left: 24, right: 24 }}
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
            tickValues: [0, 25, 50, 75, 100],
            formatYLabel: (v) => pct(Number(v)),
            labelColor: theme.text,
            lineColor: theme.grid,
          },
        ]}
        frame={{ lineWidth: 0 }}
      >
        {({ points, chartBounds }) => {
          const barWidth = Math.max(
            4,
            ((chartBounds.right - chartBounds.left) / Math.max(rows.length, 1)) * 0.6,
          );
          const stacks = keys.map((k) => points[k] ?? []);
          return (
            <StackedBar
              points={stacks}
              chartBounds={chartBounds}
              barWidth={barWidth}
              colors={colors}
              barOptions={({ columnIndex }) =>
                rows[columnIndex]?.low_sample ? { opacity: 0.35 } : {}
              }
            />
          );
        }}
      </CartesianChart>
    </ChartFrame>
  );
}
