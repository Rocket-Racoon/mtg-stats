/**
 * Optional sample-size fields. They match the stats API row shape (`games`, `low_sample`), so a
 * response row can be passed as it is. Both are optional: phase 1 charts (F-4.6) carry neither.
 */
export interface SampleInfo {
  games?: number;
  low_sample?: boolean;
}

/** One bar (bar chart) or point (line chart). */
export interface ChartRow extends SampleInfo {
  label: string;
  value: number;
}

export interface StackedSeries {
  key: string;
  label: string;
}

/** One 100% stacked bar. Segment values are raw amounts; the chart turns them into shares. */
export interface StackedRow extends SampleInfo {
  label: string;
  segments: Record<string, number>;
}

/** Props every kit chart shares. */
export interface ChartCommonProps {
  /** Announced as the chart's name and shown above it. */
  title: string;
  /** Shown instead of empty axes when there are no rows. */
  emptyMessage?: string;
  height?: number;
  testID?: string;
}
