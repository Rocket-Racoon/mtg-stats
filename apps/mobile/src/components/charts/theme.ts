import { useColorScheme } from 'react-native';

/** Colors every kit chart draws with. Light and dark follow the OS color scheme. */
export interface ChartTheme {
  background: string;
  text: string;
  mutedText: string;
  grid: string;
  /** Outline for small-sample marks and the list toggle; at least 3:1 against the background. */
  border: string;
  tooltipBackground: string;
  tooltipText: string;
  /** The text color, not a series hue, so it stands out from the bars; it is also dashed and named in the legend. */
  referenceLine: string;
  /** Series palette. Chart meaning never rests on these alone: labels, text and the list view carry it too. */
  series: readonly string[];
}

export const LIGHT_CHART_THEME: ChartTheme = {
  background: '#ffffff',
  text: '#111827',
  mutedText: '#4b5563',
  grid: '#d1d5db',
  border: '#6b7280',
  tooltipBackground: '#1f2937',
  tooltipText: '#ffffff',
  referenceLine: '#111827',
  series: ['#4f46e5', '#0f766e', '#b45309', '#be185d', '#475569', '#7c3aed'],
};

export const DARK_CHART_THEME: ChartTheme = {
  background: '#111827',
  text: '#f9fafb',
  mutedText: '#d1d5db',
  grid: '#374151',
  border: '#9ca3af',
  tooltipBackground: '#f3f4f6',
  tooltipText: '#111827',
  referenceLine: '#f9fafb',
  series: ['#818cf8', '#5eead4', '#fbbf24', '#f9a8d4', '#cbd5e1', '#c4b5fd'],
};

export function chartThemeFor(scheme: 'light' | 'dark' | null | undefined): ChartTheme {
  return scheme === 'dark' ? DARK_CHART_THEME : LIGHT_CHART_THEME;
}

export function useChartTheme(): ChartTheme {
  return chartThemeFor(useColorScheme());
}
