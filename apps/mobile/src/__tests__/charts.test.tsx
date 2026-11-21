import { fireEvent, render, screen } from '@testing-library/react-native';
import { mockPressed } from './chart-mocks';
import { BarChart, LineChart, StackedBarChart } from '../components/charts';
import { toShares } from '../components/charts/StackedBarChart';

const rows = [
  { label: 'Red', value: 60, games: 20 },
  { label: 'Blue', value: 55, games: 3, low_sample: true },
  { label: 'Green', value: 40, games: 12 },
  { label: 'White', value: 10, games: 9 },
];

const colorSeries = [
  { key: 'r', label: 'Red' },
  { key: 'u', label: 'Blue' },
];

beforeEach(() => {
  mockPressed.index = -1;
});

describe('chart summary label', () => {
  it('announces the title and the top three values', async () => {
    await render(<BarChart title="Win rate by color" rows={rows} />);
    expect(
      screen.getByLabelText('Win rate by color. Top values: Red 60, Blue 55, Green 40.'),
    ).toBeTruthy();
  });

  it('mentions the reference line', async () => {
    await render(
      <BarChart title="Curve" rows={rows} referenceLine={{ value: 50, label: 'Average' }} />,
    );
    expect(screen.getByLabelText(/Reference: Average 50\./)).toBeTruthy();
    expect(screen.getByText('Dashed line: Average 50')).toBeTruthy();
    expect(screen.getByTestId('skia-line')).toBeTruthy();
  });

  it('summarizes the line chart', async () => {
    await render(<LineChart title="Win rate over time" rows={rows} />);
    expect(screen.getByLabelText(/^Win rate over time\. Top values: Red 60/)).toBeTruthy();
  });

  it('summarizes the stacked chart with the largest shares', async () => {
    await render(
      <StackedBarChart
        title="Colors"
        series={colorSeries}
        rows={[{ label: 'Deck A', segments: { r: 3, u: 1 } }]}
      />,
    );
    expect(
      screen.getByLabelText('Colors. Top values: Deck A Red 75%, Deck A Blue 25%.'),
    ).toBeTruthy();
  });
});

describe('show as list toggle', () => {
  it('swaps the chart for a list of the same data and back', async () => {
    await render(<BarChart title="Win rate by color" rows={rows} />);
    expect(screen.queryByTestId('chart-list')).toBeNull();
    await fireEvent.press(screen.getByLabelText('Show as list'));
    expect(screen.getByTestId('chart-list')).toBeTruthy();
    expect(screen.getByText('Red: 60, 20 games')).toBeTruthy();
    expect(screen.getByText('Blue: 55, 3 games, low sample')).toBeTruthy();
    expect(screen.queryByTestId('victory-chart')).toBeNull();
    await fireEvent.press(screen.getByLabelText('Show as chart'));
    expect(screen.getByTestId('victory-chart')).toBeTruthy();
  });

  it('lists every stacked segment with its percentage', async () => {
    await render(
      <StackedBarChart
        title="Colors"
        series={colorSeries}
        rows={[{ label: 'Deck A', segments: { r: 3, u: 1 }, games: 4 }]}
      />,
    );
    await fireEvent.press(screen.getByLabelText('Show as list'));
    expect(screen.getByText('Deck A: Red 75%, Blue 25%, 4 games')).toBeTruthy();
  });
});

describe('low-sample styling', () => {
  it('draws low-sample bars faded, notes them, and keeps solid bars solid', async () => {
    await render(<BarChart title="T" rows={rows} />);
    const bars = screen.getAllByTestId('victory-bar');
    expect(bars[0]?.props.count).toBe(3);
    expect(bars[0]?.props.opacity).toBeUndefined();
    expect(bars[1]?.props.count).toBe(1);
    expect(bars[1]?.props.opacity).toBeLessThan(0.5);
    expect(screen.getByText(/Low sample/)).toBeTruthy();
  });

  it('draws low-sample line points smaller and faded', async () => {
    await render(<LineChart title="T" rows={rows} />);
    const dots = screen.getAllByTestId('victory-scatter');
    expect(dots[0]?.props.radius).toBeGreaterThan(dots[1]?.props.radius as number);
    expect(dots[1]?.props.opacity).toBeLessThan(0.5);
  });

  it('shows no low-sample note when rows carry no sample info', async () => {
    await render(<BarChart title="T" rows={[{ label: 'a', value: 1 }]} />);
    expect(screen.queryByText(/Low sample/)).toBeNull();
  });
});

describe('tap tooltip', () => {
  it('shows the value and the number of games for the mockPressed bar', async () => {
    mockPressed.index = 1;
    await render(<BarChart title="T" rows={rows} />);
    expect(screen.getByTestId('chart-tooltip').props.children).toBe(
      'Blue: 55, 3 games, low sample',
    );
  });

  it('shows only the value when the row has no games', async () => {
    mockPressed.index = 0;
    await render(<LineChart title="T" rows={[{ label: 'a', value: 2.5 }]} />);
    expect(screen.getByTestId('chart-tooltip').props.children).toBe('a: 2.5');
  });
});

describe('empty state', () => {
  it('shows the message instead of the chart', async () => {
    await render(<BarChart title="T" rows={[]} emptyMessage="No games yet" />);
    expect(screen.getByText('No games yet')).toBeTruthy();
    expect(screen.queryByTestId('victory-chart')).toBeNull();
    expect(screen.queryByLabelText('Show as list')).toBeNull();
  });

  it('has a default message for every chart', async () => {
    await render(<LineChart title="T" rows={[]} />);
    expect(screen.getByTestId('chart-empty')).toBeTruthy();
  });
});

describe('toShares', () => {
  it('turns segments into percentages and survives a zero total', async () => {
    const series = [
      { key: 'a', label: 'A' },
      { key: 'b', label: 'B' },
    ];
    expect(toShares({ label: 'x', segments: { a: 1, b: 3 } }, series)).toEqual({ a: 25, b: 75 });
    expect(toShares({ label: 'x', segments: {} }, series)).toEqual({ a: 0, b: 0 });
  });
});
