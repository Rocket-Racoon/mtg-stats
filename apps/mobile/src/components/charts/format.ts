import type { ChartRow, SampleInfo, StackedRow, StackedSeries } from './types';

export const DEFAULT_EMPTY_MESSAGE = 'No data to show yet.';

export function defaultFormatValue(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

export function gamesText(games: number): string {
  return `${games} ${games === 1 ? 'game' : 'games'}`;
}

/**
 * Sample-size wording for the tooltip and the list: "20 games", "Small sample: 3 games",
 * "Small sample: 1 game". Empty when the row has no sample info.
 */
export function sampleText(info: SampleInfo): string {
  if (info.low_sample) {
    return info.games !== undefined ? `Small sample: ${gamesText(info.games)}` : 'Small sample';
  }
  return info.games !== undefined ? gamesText(info.games) : '';
}

export function isFiniteNumber(value: number): boolean {
  return Number.isFinite(value);
}

/** The height a value is drawn at: zero for a negative, null (nothing) for a non-finite value. */
export function drawnValue(value: number): number | null {
  return Number.isFinite(value) ? Math.max(value, 0) : null;
}

/** "Red: 60, 20 games" or "Blue: 55, Small sample: 3 games" or "Red: no value". */
export function rowText(row: ChartRow, formatValue: (value: number) => string): string {
  const value = Number.isFinite(row.value) ? formatValue(row.value) : 'no value';
  return [`${row.label}: ${value}`, sampleText(row)].filter(Boolean).join(', ');
}

/** Largest max length of an x-axis label, by how many labels share the axis. */
function maxLabelChars(count: number): number {
  if (count <= 4) return 12;
  if (count <= 6) return 8;
  if (count <= 10) return 5;
  return 3;
}

/**
 * The axis label of a row: shortened to fit the row count (full label stays in the tooltip, list
 * and summary) and suffixed with "*" for a small sample, so the marker is never cut off.
 */
export function axisLabel(label: string, lowSample: boolean | undefined, count: number): string {
  const max = maxLabelChars(count);
  const text = label.length > max ? `${label.slice(0, Math.max(max - 1, 1))}…` : label;
  return lowSample ? `${text}*` : text;
}

/**
 * Whole-number percentages of the row total that add up to exactly 100 (largest remainder), or all
 * zeros when the row total is zero. Negative and non-finite segments count as zero.
 */
export function displayShares(
  row: StackedRow,
  series: readonly StackedSeries[],
): Record<string, number> {
  const raw = series.map((s) => {
    const v = row.segments[s.key] ?? 0;
    return Number.isFinite(v) ? Math.max(v, 0) : 0;
  });
  const total = raw.reduce((a, b) => a + b, 0);
  const out: Record<string, number> = {};
  if (total <= 0) {
    for (const s of series) out[s.key] = 0;
    return out;
  }
  const exact = raw.map((v) => (v / total) * 100);
  const floors = exact.map(Math.floor);
  let left = 100 - floors.reduce((a, b) => a + b, 0);
  const order = exact
    .map((v, i) => ({ i, frac: v - Math.floor(v) }))
    .sort((a, b) => b.frac - a.frac || a.i - b.i);
  for (const { i } of order) {
    if (left <= 0) break;
    floors[i] = (floors[i] ?? 0) + 1;
    left -= 1;
  }
  series.forEach((s, i) => {
    out[s.key] = floors[i] ?? 0;
  });
  return out;
}

/** Title plus the highest `count` entries, for the screen-reader summary. */
export function summarize(
  title: string,
  entries: readonly { label: string; value: number; small?: boolean }[],
  formatValue: (value: number) => string,
  count = 3,
): string {
  const finite = entries.filter((e) => Number.isFinite(e.value));
  if (finite.length === 0) return `${title}. No data.`;
  const top = [...finite]
    .sort((a, b) => b.value - a.value)
    .slice(0, count)
    .map((e) => `${e.label} ${formatValue(e.value)}${e.small ? ' (small sample)' : ''}`)
    .join(', ');
  return `${title}. Top values: ${top}.`;
}
