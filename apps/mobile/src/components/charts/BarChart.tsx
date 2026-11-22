import { DashPathEffect, Line as SkiaLine, vec } from '@shopify/react-native-skia';
import { useMemo } from 'react';
import { Bar, CartesianChart } from 'victory-native';
import { ChartFrame } from './ChartFrame';
import { axisLabel, defaultFormatValue, drawnValue, rowText, summarize } from './format';
import { SmallSampleOutline } from './SmallSampleOutline';
import { useChartTheme } from './theme';
import type { ChartCommonProps, ChartRow } from './types';
import { useChartFont } from './useChartFont';
import { useTapSelection } from './useTapSelection';

export interface ReferenceLine {
  value: number;
  label: string;
}

export interface BarChartProps extends ChartCommonProps {
  rows: readonly ChartRow[];
  /**
   * A dashed horizontal line at `value`, named by `label` in the legend, summary and list. A
   * negative value is drawn at zero (its real number stays in the text); a non-finite value
   * draws no line.
   */
  referenceLine?: ReferenceLine;
  formatValue?: (value: number) => string;
}

/**
 * Bar chart with an optional reference line. Small-sample bars are faded, outlined with a dashed
 * border and marked with "*". Tapping a bar shows its value and "n games" and keeps it selected.
 */
export function BarChart({
  title,
  rows,
  referenceLine,
  formatValue = defaultFormatValue,
  emptyMessage,
  height = 220,
  testID,
  onSelect,
}: BarChartProps) {
  const theme = useChartTheme();
  const font = useChartFont();
  const { selected, gesture, reportGeometry, clear } = useTapSelection(rows.length, onSelect);
  const data = useMemo(
    () => rows.map((r, index) => ({ index, value: drawnValue(r.value) })),
    [rows],
  );
  const ticks = useMemo(() => rows.map((_, i) => i), [rows]);
  const row = selected === null ? undefined : rows[selected];
  const reference =
    referenceLine && Number.isFinite(referenceLine.value) ? referenceLine : undefined;
  const referenceText = reference ? `${reference.label} ${formatValue(reference.value)}` : null;
  const yMax = Math.max(
    1,
    reference ? Math.max(reference.value, 0) : 0,
    ...data.map((d) => d.value ?? 0),
  );

  return (
    <ChartFrame
      title={title}
      testID={testID}
      height={height}
      emptyMessage={emptyMessage}
      isEmpty={!rows.some((r) => Number.isFinite(r.value))}
      summary={
        summarize(
          title,
          rows.map((r) => ({ label: r.label, value: r.value, small: r.low_sample })),
          formatValue,
        ) + (referenceText ? ` Reference: ${referenceText}.` : '')
      }
      items={[
        ...rows.map((r, i) => ({ key: `${i}-${r.label}`, text: rowText(r, formatValue) })),
        ...(referenceText ? [{ key: 'reference', text: `Reference: ${referenceText}` }] : []),
      ]}
      tooltip={row ? rowText(row, formatValue) : null}
      hasLowSample={rows.some((r) => r.low_sample)}
      legend={
        referenceText
          ? [
              {
                text: `Dashed line: ${referenceText}`,
                swatch: { color: theme.referenceLine, dashed: true },
              },
            ]
          : []
      }
      gesture={gesture}
      onShowList={clear}
    >
      <CartesianChart
        data={data}
        xKey="index"
        yKeys={['value']}
        domain={{ y: [0, yMax] }}
        domainPadding={{ left: 24, right: 24, top: 8 }}
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
        {({ points, chartBounds, yScale }) => {
          const slot = (chartBounds.right - chartBounds.left) / Math.max(rows.length, 1);
          const barWidth = Math.max(4, slot * 0.6);
          reportGeometry(
            points.value.map((p) => p.x),
            chartBounds.left,
            chartBounds.right,
          );
          const drawn = points.value.filter((p) => p.y !== null && p.y !== undefined);
          const isSmall = (p: (typeof drawn)[number]) => {
            const idx = points.value.indexOf(p);
            return rows[idx]?.low_sample === true;
          };
          const solid = drawn.filter((p) => !isSmall(p));
          const faded = drawn.filter(isSmall);
          const refY = reference ? yScale(Math.max(reference.value, 0)) : null;
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
              {faded.map((p) => (
                <SmallSampleOutline
                  key={p.x}
                  x={p.x - barWidth / 2}
                  y={p.y ?? chartBounds.bottom}
                  width={barWidth}
                  height={chartBounds.bottom - (p.y ?? chartBounds.bottom)}
                  color={theme.border}
                />
              ))}
              {refY === null ? null : (
                <SkiaLine
                  p1={vec(chartBounds.left, refY)}
                  p2={vec(chartBounds.right, refY)}
                  color={theme.referenceLine}
                  style="stroke"
                  strokeWidth={3}
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
