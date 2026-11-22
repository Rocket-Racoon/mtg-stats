import { Paint } from '@shopify/react-native-skia';
import { useMemo } from 'react';
import { CartesianChart, StackedBar } from 'victory-native';
import { ChartFrame } from './ChartFrame';
import { axisLabel, displayShares, sampleText, summarize } from './format';
import { SmallSampleOutline } from './SmallSampleOutline';
import { useChartTheme } from './theme';
import type { ChartCommonProps, StackedRow, StackedSeries } from './types';
import { useChartFont } from './useChartFont';
import { useTapSelection } from './useTapSelection';

export interface StackedBarChartProps extends ChartCommonProps {
  /** The segments, bottom to top. Every row's `segments` is keyed by `series[].key`. */
  series: readonly StackedSeries[];
  rows: readonly StackedRow[];
}

type Shares = Record<string, number>;

/** Segment amount as drawn: negative and non-finite amounts count as zero. */
function amount(row: StackedRow, key: string): number {
  const v = row.segments[key] ?? 0;
  return Number.isFinite(v) ? Math.max(v, 0) : 0;
}

/** Each row's segments as exact percentages of the row total (all zero when the total is zero). */
export function toShares(row: StackedRow, series: readonly StackedSeries[]): Shares {
  const total = series.reduce((sum, s) => sum + amount(row, s.key), 0);
  const out: Shares = {};
  for (const s of series) out[s.key] = total > 0 ? (amount(row, s.key) / total) * 100 : 0;
  return out;
}

const pct = (v: number) => `${Math.round(v)}%`;

function rowText(row: StackedRow, series: readonly StackedSeries[], shares: Shares): string {
  const total = series.reduce((sum, s) => sum + amount(row, s.key), 0);
  const parts =
    total > 0 ? series.map((s) => `${s.label} ${pct(shares[s.key] ?? 0)}`).join(', ') : 'no data';
  return [`${row.label}: ${parts}`, sampleText(row)].filter(Boolean).join(', ');
}

// Internal Victory keys are prefixed, so a series named "index" can never clash with the x key.
const seriesKey = (key: string) => `series:${key}`;

/**
 * 100% stacked bar chart. Every bar fills the axis and each segment is its share of the row; the
 * shown percentages are whole numbers that add up to 100. Thin separators split the segments, and
 * the legend (swatches plus names), the tooltip and the list name each segment, so color is not
 * the only way to tell them apart. Small-sample bars are faded, outlined and marked with "*".
 * Tapping a bar shows its shares and "n games" and keeps it selected.
 */
export function StackedBarChart({
  title,
  series,
  rows,
  emptyMessage,
  height = 220,
  testID,
  onSelect,
}: StackedBarChartProps) {
  const theme = useChartTheme();
  const font = useChartFont();
  const { selected, gesture, reportGeometry, clear } = useTapSelection(rows.length, onSelect);
  const keys = useMemo(() => series.map((s) => seriesKey(s.key)), [series]);
  const exact = useMemo(() => rows.map((r) => toShares(r, series)), [rows, series]);
  const shown = useMemo(() => rows.map((r) => displayShares(r, series)), [rows, series]);
  const data = useMemo(
    () =>
      rows.map((_, index) => {
        const row: Record<string, number> = {};
        series.forEach((s) => {
          row[seriesKey(s.key)] = exact[index]?.[s.key] ?? 0;
        });
        row.index = index;
        return row;
      }),
    [rows, series, exact],
  );
  const ticks = useMemo(() => rows.map((_, i) => i), [rows]);
  const colors = series.map((_, i) => theme.series[i % theme.series.length] ?? theme.text);
  const hasData = rows.some((r) => series.some((s) => amount(r, s.key) > 0));

  // Top values for the summary: the largest segment shares across all rows.
  const entries = rows.flatMap((r, i) =>
    series.map((s) => ({
      label: `${r.label} ${s.label}`,
      value: shown[i]?.[s.key] ?? 0,
      small: r.low_sample,
    })),
  );
  const selectedRow = selected === null ? undefined : rows[selected];
  const selectedShares = selected === null ? undefined : shown[selected];

  return (
    <ChartFrame
      title={title}
      testID={testID}
      height={height}
      emptyMessage={emptyMessage}
      isEmpty={!hasData || series.length === 0}
      summary={summarize(title, entries, pct)}
      items={rows.map((r, i) => ({
        key: `${i}-${r.label}`,
        text: rowText(r, series, shown[i] ?? {}),
      }))}
      tooltip={selectedRow && selectedShares ? rowText(selectedRow, series, selectedShares) : null}
      hasLowSample={rows.some((r) => r.low_sample)}
      legend={[{ text: `Segments, bottom to top: ${series.map((s) => s.label).join(', ')}` }]}
      swatches={series.map((s, i) => ({ label: s.label, color: colors[i] ?? theme.text }))}
      gesture={gesture}
      onShowList={clear}
    >
      <CartesianChart
        data={data}
        xKey="index"
        yKeys={keys}
        domain={{ y: [0, 100] }}
        domainPadding={{ left: 24, right: 24 }}
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
            tickValues: [0, 25, 50, 75, 100],
            formatYLabel: (v) => pct(Number(v)),
            labelColor: theme.text,
            lineColor: theme.grid,
          },
        ]}
        frame={{ lineWidth: 0 }}
      >
        {({ points, chartBounds, yScale }) => {
          const slot = (chartBounds.right - chartBounds.left) / Math.max(rows.length, 1);
          const barWidth = Math.max(4, slot * 0.6);
          const stacks = keys.map((k) => points[k] ?? []);
          const columns = stacks[0] ?? [];
          reportGeometry(
            columns.map((p) => p.x),
            chartBounds.left,
            chartBounds.right,
          );
          return (
            <>
              <StackedBar
                points={stacks}
                chartBounds={chartBounds}
                barWidth={barWidth}
                colors={colors}
                barOptions={({ columnIndex }) => ({
                  ...(rows[columnIndex]?.low_sample ? { opacity: 0.35 } : {}),
                  // A thin background-colored edge separates neighbouring segments.
                  children: <Paint style="stroke" strokeWidth={2} color={theme.background} />,
                })}
              />
              {columns.map((p, i) =>
                rows[i]?.low_sample && hasRowData(rows[i], series) ? (
                  <SmallSampleOutline
                    key={p.x}
                    x={p.x - barWidth / 2}
                    y={yScale(100)}
                    width={barWidth}
                    height={yScale(0) - yScale(100)}
                    color={theme.border}
                  />
                ) : null,
              )}
            </>
          );
        }}
      </CartesianChart>
    </ChartFrame>
  );
}

function hasRowData(row: StackedRow | undefined, series: readonly StackedSeries[]): boolean {
  return row !== undefined && series.some((s) => amount(row, s.key) > 0);
}
