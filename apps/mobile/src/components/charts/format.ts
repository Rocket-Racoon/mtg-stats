import type { SampleInfo } from './types';

export const DEFAULT_EMPTY_MESSAGE = 'No data to show yet.';

export function defaultFormatValue(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

export function gamesText(games: number): string {
  return `${games} ${games === 1 ? 'game' : 'games'}`;
}

/** "n games" (and "low sample") for the tooltip and the list; empty when the row has no sample info. */
export function sampleText(info: SampleInfo): string {
  const parts: string[] = [];
  if (info.games !== undefined) parts.push(gamesText(info.games));
  if (info.low_sample) parts.push('low sample');
  return parts.join(', ');
}

/** Title plus the highest `count` entries, for the screen-reader summary. */
export function summarize(
  title: string,
  entries: readonly { label: string; value: number }[],
  formatValue: (value: number) => string,
  count = 3,
): string {
  if (entries.length === 0) return `${title}. No data.`;
  const top = [...entries]
    .sort((a, b) => b.value - a.value)
    .slice(0, count)
    .map((e) => `${e.label} ${formatValue(e.value)}`)
    .join(', ');
  return `${title}. Top values: ${top}.`;
}
