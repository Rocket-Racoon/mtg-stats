/**
 * Optional sample-size fields. They match the stats API row shape (`games`, `low_sample`), so a
 * response row can be passed as it is. Both are optional: phase 1 charts (F-4.6) carry neither.
 * `low_sample` is the API field name; the user-facing wording is "Small sample".
 */
export interface SampleInfo {
  games?: number;
  low_sample?: boolean;
}

/**
 * One bar (bar chart) or point (line chart).
 *
 * The kit's consumers are counts and shares, so values are drawn from zero upward:
 *  - a negative value is drawn as zero height (its real number still shows in the tooltip, the
 *    list and the summary);
 *  - a `NaN` or infinite value means "no value": nothing is drawn for that row, the list says
 *    "no value", and the summary and the axis range ignore it.
 */
export interface ChartRow extends SampleInfo {
  label: string;
  value: number;
}

export interface StackedSeries {
  key: string;
  label: string;
}

/**
 * One 100% stacked bar. Segment values are raw amounts; the chart turns them into shares.
 * A negative, `NaN` or infinite segment counts as zero. A row whose segments total zero has no
 * bar; if every row totals zero the chart shows the empty message.
 */
export interface StackedRow extends SampleInfo {
  label: string;
  segments: Record<string, number>;
}

/** Props every kit chart shares. */
export interface ChartCommonProps {
  /** Announced as the chart's name and shown above it. */
  title: string;
  /** Shown instead of empty axes when there is nothing to draw. */
  emptyMessage?: string;
  height?: number;
  testID?: string;
  /**
   * Called when a tap selects a bar or point (its index in `rows`), and with `null` when the
   * selection is cleared (tap on the selected mark again, or switching to the list).
   */
  onSelect?: (index: number | null) => void;
}
