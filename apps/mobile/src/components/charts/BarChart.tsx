import { DashPathEffect, Line as SkiaLine, vec } from '@shopify/react-native-skia';
import { useMemo } from 'react';
import { Bar, CartesianChart, useChartPressState } from 'victory-native';
import { ChartFrame } from './ChartFrame';
import { defaultFormatValue, sampleText, summarize } from './format';
import { useChartTheme } from './theme';
import type { ChartCommonProps, ChartRow } from './types';
import { useChartFont } from './useChartFont';
import { useSelectedIndex } from './useSelectedIndex';

export interface ReferenceLine {
  value: number;
  label: string;
}

export interface BarChartProps extends ChartCommonProps {
  rows: readonly ChartRow[];
  /** A dashed horizontal line at `value`, named by `label` in the legend and the summary. */
  referenceLine?: ReferenceLine;
  formatValue?: (value: number) => string;
}

function rowText(row: ChartRow, formatValue: (v: number) => string): string {
  return [`${row.label}: ${formatValue(row.value)}`, sampleText(row)].filter(Boolean).join(', ');
}

/** Bar chart with an optional reference line. Low-sample bars are faded and marked with "*". */
export function BarChart({
  title,
  rows,
  referenceLine,
  formatValue = defaultFormatValue,
  emptyMessage,
  height = 220,
  testID,
}: BarChartProps) {
  const theme = useChartTheme();
  const font = useChartFont();
  const { state } = useChartPressState({ x: 0, y: { value: 0 } });
  const selected = useSelectedIndex(state);
  const data = useMemo(() => rows.map((r, index) => ({ index, value: r.value })), [rows]);
  const ticks = useMemo(() => rows.map((_, i) => i), [rows]);
  const labelFor = (index: number) => {
    const row = rows[index];
    return row ? `${row.label}${row.low_sample ? '*' : ''}` : '';
  };
  const row = selected === null ? undefined : rows[selected];
  const referenceText = referenceLine
    ? `${referenceLine.label} ${formatValue(referenceLine.value)}`
    : null;

  return (
    <ChartFrame
      title={title}
      testID={testID}
      height={height}
      emptyMessage={emptyMessage}
      isEmpty={rows.length === 0}
      summary={
        summarize(title, rows, formatValue) + (referenceText ? ` Reference: ${referenceText}.` : '')
      }
      items={[
        ...rows.map((r, i) => ({ key: `${i}-${r.label}`, text: rowText(r, formatValue) })),
        ...(referenceText ? [{ key: 'reference', text: `Reference: ${referenceText}` }] : []),
      ]}
      tooltip={row ? rowText(row, formatValue) : null}
      hasLowSample={rows.some((r) => r.low_sample)}
      legend={referenceText ? [`Dashed line: ${referenceText}`] : []}
    >
      <CartesianChart
        data={data}
        xKey="index"
        yKeys={['value']}
        domain={{ y: [0, Math.max(referenceLine?.value ?? 0, ...rows.map((r) => r.value), 1)] }}
        domainPadding={{ left: 24, right: 24, top: 8 }}
        chartPressState={state}
        xAxis={{
          font,
          tickValues: ticks,
          formatXLabel: (v) => labelFor(Number(v)),
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
        {({ points, chartBounds, yScale }) => {
          const barWidth = Math.max(
            4,
            ((chartBounds.right - chartBounds.left) / Math.max(rows.length, 1)) * 0.6,
          );
          const solid = points.value.filter((_, i) => !rows[i]?.low_sample);
          const faded = points.value.filter((_, i) => rows[i]?.low_sample);
          const refY = referenceLine ? yScale(referenceLine.value) : null;
          return (
            <>
              <Bar
                points={solid}
                chartBounds={chartBounds}
                barWidth={barWidth}
                color={theme.series[0]}
                roundedCorners={{ topLeft: 3, topRight: 3 }}
              />
              <Bar
                points={faded}
                chartBounds={chartBounds}
                barWidth={barWidth}
                color={theme.series[0]}
                opacity={0.35}
                roundedCorners={{ topLeft: 3, topRight: 3 }}
              />
              {refY === null ? null : (
                <SkiaLine
                  p1={vec(chartBounds.left, refY)}
                  p2={vec(chartBounds.right, refY)}
                  color={theme.referenceLine}
                  style="stroke"
                  strokeWidth={2}
                >
                  <DashPathEffect intervals={[8, 6]} />
                </SkiaLine>
              )}
            </>
          );
        }}
      </CartesianChart>
    </ChartFrame>
  );
}
