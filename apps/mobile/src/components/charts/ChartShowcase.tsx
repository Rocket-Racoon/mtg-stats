import { ScrollView } from 'react-native';
import { BarChart, LineChart, StackedBarChart } from '.';

/** Dev-only sample screen for checking the kit on a device or emulator. */
export function ChartShowcase() {
  return (
    <ScrollView contentContainerStyle={{ gap: 16, padding: 8 }}>
      <BarChart
        title="Mana curve"
        rows={[
          { label: '1', value: 6 },
          { label: '2', value: 12 },
          { label: '3', value: 9 },
          { label: '4', value: 5 },
          { label: '5', value: 2 },
        ]}
        referenceLine={{ value: 8, label: 'Typical' }}
      />
      <LineChart
        title="Win rate by month"
        formatValue={(v) => `${Math.round(v)}%`}
        rows={[
          { label: 'Jan', value: 40, games: 10 },
          { label: 'Feb', value: 55, games: 2, low_sample: true },
          { label: 'Mar', value: 48, games: 12 },
          { label: 'Apr', value: 62, games: 9 },
        ]}
      />
      <StackedBarChart
        title="Colors by deck"
        series={[
          { key: 'w', label: 'White' },
          { key: 'u', label: 'Blue' },
          { key: 'r', label: 'Red' },
        ]}
        rows={[
          { label: 'A', segments: { w: 4, u: 2, r: 6 }, games: 14 },
          { label: 'B', segments: { w: 1, u: 8, r: 1 }, games: 2, low_sample: true },
        ]}
      />
      <BarChart title="Empty example" rows={[]} />
    </ScrollView>
  );
}
