/* eslint-disable @typescript-eslint/no-require-imports */
// S-7.2.8 acceptance tests for the mobile chart kit. Skia and Victory need a native canvas, so they
// are replaced here by Views that expose what the kit hands them. Unlike chart-mocks.tsx, this mock
// also runs the kit's own axis formatters and per-column bar options, so what is asserted about the
// "*" axis marker and stacked-bar fading comes from the kit's code and not from the mock.
import { fireEvent, render, screen, within } from '@testing-library/react-native';
import type { ReactElement, ReactNode } from 'react';
import {
  BarChart,
  chartThemeFor,
  LineChart,
  StackedBarChart,
  type ChartRow,
  type StackedRow,
} from '../components/charts';

const mockState = { pressed: -1, scheme: 'light' as 'light' | 'dark' };

jest.mock('react-native/Libraries/Utilities/useColorScheme', () => ({
  __esModule: true,
  default: () => mockState.scheme,
}));

jest.mock('@shopify/react-native-skia', () => {
  const { View } = require('react-native');
  return {
    matchFont: () => ({}),
    vec: (x: number, y: number) => ({ x, y }),
    Line: (props: object) => <View testID="skia-line" {...props} />,
    DashPathEffect: () => null,
  };
});

jest.mock('react-native-reanimated', () => {
  const actual = jest.requireActual('react-native-reanimated');
  const { useEffect } = require('react');
  return {
    ...actual,
    useAnimatedReaction: (prepare: () => number, react: (v: number, p: number | null) => void) => {
      useEffect(() => {
        react(prepare(), null);
        // eslint-disable-next-line react-hooks/exhaustive-deps
      }, []);
    },
  };
});

jest.mock('victory-native', () => {
  const { View, Text } = require('react-native');
  const point = (x: number, y: number) => ({ x, xValue: x, y, yValue: y });
  const Part = (testID: string) =>
    function MockPart(props: {
      points: unknown;
      opacity?: number;
      radius?: number;
      color?: string;
    }) {
      return (
        <View
          testID={testID}
          count={Array.isArray(props.points) ? props.points.length : 0}
          opacity={props.opacity}
          radius={props.radius}
          color={props.color}
        />
      );
    };
  return {
    useChartPressState: (init: { y: Record<string, number> }) => ({
      state: {
        isActive: { get: () => mockState.pressed >= 0 },
        matchedIndex: { get: () => mockState.pressed },
        x: {},
        y: Object.fromEntries(Object.keys(init.y).map((k) => [k, {}])),
      },
      isActive: false,
    }),
    CartesianChart: (props: {
      data: Record<string, number>[];
      yKeys: string[];
      xAxis: { tickValues: number[]; formatXLabel: (v: number) => string };
      children: (a: unknown) => ReactNode;
    }) => {
      const points = Object.fromEntries(
        props.yKeys.map((k) => [k, props.data.map((d, i) => point(i, d[k] ?? 0))]),
      );
      return (
        <View testID="victory-chart">
          {props.xAxis.tickValues.map((t) => (
            <Text key={t} testID="x-label">
              {props.xAxis.formatXLabel(t)}
            </Text>
          ))}
          {props.children({
            points,
            chartBounds: { left: 0, right: 100, top: 0, bottom: 100 },
            yScale: (v: number) => 100 - v,
          })}
        </View>
      );
    },
    Bar: Part('victory-bar'),
    Line: Part('victory-line'),
    Scatter: Part('victory-scatter'),
    StackedBar: (props: {
      points: unknown[][];
      colors: string[];
      barOptions: (a: { columnIndex: number }) => { opacity?: number };
    }) => (
      <View testID="victory-stacked-bar" colors={props.colors}>
        {(props.points[0] ?? []).map((_, columnIndex) => (
          <View
            key={columnIndex}
            testID="stacked-column"
            opacity={props.barOptions({ columnIndex }).opacity}
          />
        ))}
      </View>
    ),
  };
});

const rows: ChartRow[] = [
  { label: 'Red', value: 60, games: 20, low_sample: false },
  { label: 'Blue', value: 55, games: 3, low_sample: true },
  { label: 'Green', value: 40, games: 12, low_sample: false },
  { label: 'White', value: 10, games: 9, low_sample: false },
];

const series = [
  { key: 'r', label: 'Red' },
  { key: 'u', label: 'Blue' },
  { key: 'g', label: 'Green' },
];
const stackedRows: StackedRow[] = [
  { label: 'Deck A', segments: { r: 2, u: 1, g: 1 }, games: 10, low_sample: false },
  { label: 'Deck B', segments: { r: 1, u: 1, g: 2 }, games: 2, low_sample: true },
];

// Phase 1 data (F-4.6): no `games`, no `low_sample` anywhere. This also proves, at type level, that
// both props are optional for the bar and stacked charts.
const manaCurve: ChartRow[] = [
  { label: '1', value: 7 },
  { label: '2', value: 12 },
  { label: '3', value: 9 },
  { label: '4', value: 4 },
];
const colorShare: StackedRow[] = [
  { label: 'Creatures', segments: { r: 4, u: 2, g: 6 } },
  { label: 'Spells', segments: { r: 1, u: 5, g: 0 } },
];

const charts = {
  bar: () => <BarChart title="Win rate" rows={rows} />,
  line: () => <LineChart title="Win rate" rows={rows} />,
  stacked: () => <StackedBarChart title="Colors" series={series} rows={stackedRows} />,
};
type ChartName = keyof typeof charts;
const chartNames = Object.keys(charts) as ChartName[];

const childrenOf = (id: string) => screen.getByTestId(id).props.children as string;
const xLabels = () => screen.getAllByTestId('x-label').map((l) => l.props.children);

beforeEach(() => {
  mockState.pressed = -1;
  mockState.scheme = 'light';
});

describe('criterion 1: renders in light and dark', () => {
  describe.each(['light', 'dark'] as const)('%s scheme', (scheme) => {
    it.each(chartNames)('draws the %s chart with data and the scheme text color', async (name) => {
      mockState.scheme = scheme;
      await render(charts[name]());
      expect(screen.getByTestId('victory-chart')).toBeTruthy();
      expect(screen.queryByTestId('chart-empty')).toBeNull();
      expect(screen.getByRole('header').props.style).toEqual(
        expect.arrayContaining([expect.objectContaining({ color: chartThemeFor(scheme).text })]),
      );
    });
  });

  it('uses a different palette, text color and background in dark than in light', () => {
    const light = chartThemeFor('light');
    const dark = chartThemeFor('dark');
    expect(dark.background).not.toBe(light.background);
    expect(dark.text).not.toBe(light.text);
    expect(dark.grid).not.toBe(light.grid);
    expect(dark.referenceLine).not.toBe(light.referenceLine);
    expect(dark.series).not.toEqual(light.series);
    expect(dark.series.length).toBe(light.series.length);
  });

  it('treats an unknown scheme as light', () => {
    expect(chartThemeFor(null)).toEqual(chartThemeFor('light'));
    expect(chartThemeFor(undefined)).toEqual(chartThemeFor('light'));
  });

  it('paints the bar series with different colors in light and dark', async () => {
    const colorOf = async (scheme: 'light' | 'dark') => {
      mockState.scheme = scheme;
      const view = await render(<BarChart title="T" rows={rows} />);
      const color = screen.getAllByTestId('victory-bar')[0]?.props.color;
      await view.unmount();
      return color;
    };
    const light = await colorOf('light');
    const dark = await colorOf('dark');
    expect(light).toBe(chartThemeFor('light').series[0]);
    expect(dark).toBe(chartThemeFor('dark').series[0]);
    expect(light).not.toBe(dark);
  });

  it('paints the line chart series with the scheme color', async () => {
    mockState.scheme = 'dark';
    await render(<LineChart title="T" rows={rows} />);
    expect(screen.getByTestId('victory-line').props.color).toBe(chartThemeFor('dark').series[0]);
  });

  it('gives the stacked chart one distinct color per segment, taken from the scheme', async () => {
    mockState.scheme = 'dark';
    await render(<StackedBarChart title="T" series={series} rows={stackedRows} />);
    const colors = screen.getByTestId('victory-stacked-bar').props.colors as string[];
    expect(colors).toEqual(chartThemeFor('dark').series.slice(0, 3));
    expect(new Set(colors).size).toBe(3);
  });

  it('draws the reference line in the scheme reference color, differing between schemes', async () => {
    const referenceColor = async (scheme: 'light' | 'dark') => {
      mockState.scheme = scheme;
      const view = await render(
        <BarChart title="T" rows={rows} referenceLine={{ value: 50, label: 'Average' }} />,
      );
      const color = screen.getByTestId('skia-line').props.color;
      await view.unmount();
      return color;
    };
    expect(await referenceColor('light')).toBe(chartThemeFor('light').referenceLine);
    expect(await referenceColor('dark')).toBe(chartThemeFor('dark').referenceLine);
  });

  it('draws the bar chart without a reference line when none is given', async () => {
    await render(<BarChart title="T" rows={rows} />);
    expect(screen.queryByTestId('skia-line')).toBeNull();
  });
});

describe('criterion 1: reference line is reachable in the summary and the list', () => {
  const ref = { value: 50, label: 'Average' };

  it('names the reference line and its value in the screen-reader summary', async () => {
    await render(<BarChart title="Win rate" rows={rows} referenceLine={ref} />);
    expect(screen.getByRole('image').props.accessibilityLabel).toContain('Average 50');
  });

  it('lists the reference line with its value in the list view', async () => {
    await render(<BarChart title="Win rate" rows={rows} referenceLine={ref} />);
    await fireEvent.press(screen.getByRole('button', { name: 'Show as list' }));
    expect(screen.getByText(/Average 50/)).toBeTruthy();
  });

  it('names the reference line in a text legend, so color is not the only cue', async () => {
    await render(<BarChart title="Win rate" rows={rows} referenceLine={ref} />);
    expect(screen.getByText(/Average 50/)).toBeTruthy();
  });

  it('keeps the reference line out of the summary and list when there is none', async () => {
    await render(<BarChart title="Win rate" rows={rows} />);
    expect(screen.getByRole('image').props.accessibilityLabel).not.toMatch(/reference/i);
    await fireEvent.press(screen.getByRole('button', { name: 'Show as list' }));
    expect(screen.queryByText(/reference/i)).toBeNull();
  });
});

describe('criterion 2: low-sample marks are de-emphasised', () => {
  it('draws low-sample bars with lower opacity than normal bars', async () => {
    await render(<BarChart title="T" rows={rows} />);
    const [solid, faded] = screen.getAllByTestId('victory-bar');
    expect(solid?.props.count).toBe(3);
    expect(faded?.props.count).toBe(1);
    expect(solid?.props.opacity ?? 1).toBe(1);
    expect(faded?.props.opacity).toBeLessThan(1);
  });

  it('draws low-sample line points smaller and with lower opacity', async () => {
    await render(<LineChart title="T" rows={rows} />);
    const [solid, faded] = screen.getAllByTestId('victory-scatter');
    expect(faded?.props.radius).toBeLessThan(solid?.props.radius as number);
    expect(faded?.props.opacity).toBeLessThan(1);
    expect(solid?.props.opacity ?? 1).toBe(1);
  });

  it('fades only the low-sample column of the stacked chart', async () => {
    await render(<StackedBarChart title="T" series={series} rows={stackedRows} />);
    const [a, b] = screen.getAllByTestId('stacked-column');
    expect(a?.props.opacity ?? 1).toBe(1);
    expect(b?.props.opacity).toBeLessThan(1);
  });

  it('marks the low-sample bar label with "*" on the axis, and only that one', async () => {
    await render(<BarChart title="T" rows={rows} />);
    expect(xLabels()).toEqual(['Red', 'Blue*', 'Green', 'White']);
  });

  it('marks the low-sample line label with "*" on the axis, and only that one', async () => {
    await render(<LineChart title="T" rows={rows} />);
    expect(xLabels()).toEqual(['Red', 'Blue*', 'Green', 'White']);
  });

  it('marks the low-sample stacked column on the axis', async () => {
    await render(<StackedBarChart title="T" series={series} rows={stackedRows} />);
    expect(xLabels()).toEqual(['Deck A', 'Deck B*']);
  });

  it('explains the marker in text when any row is low-sample', async () => {
    await render(<BarChart title="T" rows={rows} />);
    expect(screen.getByText(/low sample/i)).toBeTruthy();
  });

  it('says "low sample" in the list entry of a low-sample row only', async () => {
    await render(<BarChart title="T" rows={rows} />);
    await fireEvent.press(screen.getByRole('button', { name: 'Show as list' }));
    const list = screen.getByTestId('chart-list');
    expect(within(list).getByText(/^Blue: 55.*3 games.*low sample/)).toBeTruthy();
    expect(within(list).getByText(/^Red: 60.*20 games/).props.children).not.toMatch(/low sample/);
  });

  it('shows the value and "n games" when a bar is tapped', async () => {
    mockState.pressed = 1;
    await render(<BarChart title="T" rows={rows} />);
    const tip = childrenOf('chart-tooltip');
    expect(tip).toContain('Blue');
    expect(tip).toContain('55');
    expect(tip).toContain('3 games');
  });

  it('shows the value and "n games" when a line point is tapped', async () => {
    mockState.pressed = 1;
    await render(<LineChart title="T" rows={rows} />);
    const tip = childrenOf('chart-tooltip');
    expect(tip).toContain('Blue');
    expect(tip).toContain('55');
    expect(tip).toContain('3 games');
  });

  it('shows each segment and "n games" when a stacked bar is tapped', async () => {
    mockState.pressed = 1;
    await render(<StackedBarChart title="T" series={series} rows={stackedRows} />);
    const tip = childrenOf('chart-tooltip');
    expect(tip).toContain('Deck B');
    expect(tip).toContain('Red 25%');
    expect(tip).toContain('Green 50%');
    expect(tip).toContain('2 games');
  });

  it('shows nothing in the tooltip until a mark is tapped', async () => {
    await render(<BarChart title="T" rows={rows} />);
    expect(childrenOf('chart-tooltip')).toBe(' ');
  });

  it('uses the plural form for zero games', async () => {
    mockState.pressed = 0;
    await render(<BarChart title="T" rows={[{ label: 'A', value: 1, games: 0 }]} />);
    expect(childrenOf('chart-tooltip')).toContain('0 games');
  });

  // AMBIGUOUS: the criterion says "n games"; natural English says "1 game". This test accepts both
  // and the report flags the choice to the orchestrator.
  it('words a single game as "1 game" or "1 games" (criterion versus English)', async () => {
    mockState.pressed = 0;
    await render(
      <BarChart title="T" rows={[{ label: 'A', value: 5, games: 1, low_sample: true }]} />,
    );
    expect(childrenOf('chart-tooltip')).toMatch(/1 games?\b/);
  });
});

describe('criterion 3: accessibility', () => {
  it('announces the title and the top three values on the focusable bar chart', async () => {
    await render(charts.bar());
    const chart = screen.getByRole('image');
    expect(chart.props.accessible).toBe(true);
    const label = chart.props.accessibilityLabel as string;
    expect(label).toContain('Win rate');
    expect(label).toContain('Red 60');
    expect(label).toContain('Blue 55');
    expect(label).toContain('Green 40');
    expect(label).not.toContain('White');
  });

  it('announces the title and the top values on the line chart', async () => {
    await render(charts.line());
    const label = screen.getByRole('image').props.accessibilityLabel as string;
    expect(label).toContain('Win rate');
    expect(label).toContain('Red 60');
  });

  it('announces the title and the largest shares on the stacked chart', async () => {
    await render(charts.stacked());
    const label = screen.getByRole('image').props.accessibilityLabel as string;
    expect(label).toContain('Colors');
    expect(label).toMatch(/Deck A Red 50%/);
  });

  it('exposes the title as a header', async () => {
    await render(charts.bar());
    expect(screen.getByRole('header').props.children).toBe('Win rate');
  });

  it('sorts the top values from highest to lowest regardless of row order', async () => {
    await render(
      <BarChart
        title="T"
        rows={[
          { label: 'low', value: 1 },
          { label: 'high', value: 9 },
          { label: 'mid', value: 5 },
        ]}
      />,
    );
    const label = screen.getByRole('image').props.accessibilityLabel as string;
    expect(label.indexOf('high')).toBeLessThan(label.indexOf('mid'));
    expect(label.indexOf('mid')).toBeLessThan(label.indexOf('low'));
  });

  describe.each(chartNames)('%s chart list toggle', (name) => {
    it('has an enabled "Show as list" button', async () => {
      await render(charts[name]());
      const toggle = screen.getByRole('button', { name: 'Show as list' });
      expect(toggle.props.accessibilityState).toMatchObject({ disabled: false });
    });

    it('swaps the chart for a list and back again', async () => {
      await render(charts[name]());
      await fireEvent.press(screen.getByRole('button', { name: 'Show as list' }));
      expect(screen.getByTestId('chart-list')).toBeTruthy();
      expect(screen.queryByTestId('victory-chart')).toBeNull();
      expect(screen.queryByRole('image')).toBeNull();
      await fireEvent.press(screen.getByRole('button', { name: 'Show as chart' }));
      expect(screen.queryByTestId('chart-list')).toBeNull();
      expect(screen.getByTestId('victory-chart')).toBeTruthy();
    });
  });

  it('lists every bar row with its label, value and games, the same data as the chart', async () => {
    await render(charts.bar());
    await fireEvent.press(screen.getByRole('button', { name: 'Show as list' }));
    const list = screen.getByTestId('chart-list');
    expect(within(list).getAllByRole('text')).toHaveLength(rows.length);
    for (const r of rows) {
      expect(
        within(list).getByText(new RegExp(`^${r.label}: ${r.value}\\b.*${r.games} games`)),
      ).toBeTruthy();
    }
  });

  it('lists every line point with its label and value', async () => {
    await render(charts.line());
    await fireEvent.press(screen.getByRole('button', { name: 'Show as list' }));
    const list = screen.getByTestId('chart-list');
    expect(within(list).getAllByRole('text')).toHaveLength(rows.length);
    for (const r of rows) {
      expect(within(list).getByText(new RegExp(`^${r.label}: ${r.value}\\b`))).toBeTruthy();
    }
  });

  it('lists every stacked row with each series by name and its share', async () => {
    await render(charts.stacked());
    await fireEvent.press(screen.getByRole('button', { name: 'Show as list' }));
    const list = screen.getByTestId('chart-list');
    expect(within(list).getAllByRole('text')).toHaveLength(stackedRows.length);
    expect(within(list).getByText(/^Deck A: Red 50%, Blue 25%, Green 25%/)).toBeTruthy();
    expect(within(list).getByText(/^Deck B: Red 25%, Blue 25%, Green 50%/)).toBeTruthy();
  });

  it('names the stacked series in a text legend as well', async () => {
    await render(charts.stacked());
    expect(screen.getByText(/Red, Blue, Green/)).toBeTruthy();
  });

  it('gives the list an accessible label that names the chart', async () => {
    await render(charts.bar());
    await fireEvent.press(screen.getByRole('button', { name: 'Show as list' }));
    expect(screen.getByTestId('chart-list').props.accessibilityLabel).toContain('Win rate');
  });
});

describe('criterion 4: no data', () => {
  const empties: [string, ReactElement][] = [
    ['bar', <BarChart key="b" title="T" rows={[]} />],
    ['line', <LineChart key="l" title="T" rows={[]} />],
    ['stacked', <StackedBarChart key="s" title="T" series={series} rows={[]} />],
  ];

  it.each(empties)(
    'shows the default empty message and no axes for the %s chart',
    async (_n, el) => {
      await render(el);
      expect(childrenOf('chart-empty')).toBe('No data to show yet.');
      expect(screen.queryByTestId('victory-chart')).toBeNull();
      expect(screen.queryByTestId('x-label')).toBeNull();
      expect(screen.queryByRole('image')).toBeNull();
    },
  );

  it('shows a custom empty message and still shows the title', async () => {
    await render(<BarChart title="Win rate" rows={[]} emptyMessage="No games played yet" />);
    expect(screen.getByText('No games played yet')).toBeTruthy();
    expect(screen.getByText('Win rate')).toBeTruthy();
  });

  it('shows the empty message for a stacked chart with rows but no series', async () => {
    await render(<StackedBarChart title="T" series={[]} rows={stackedRows} />);
    expect(screen.getByTestId('chart-empty')).toBeTruthy();
    expect(screen.queryByTestId('victory-chart')).toBeNull();
  });

  // The criterion is silent on a list toggle for empty data. The kit shows none: there is nothing
  // to list and the empty message is already readable text. Flagged in the report.
  it('offers no list toggle when there is nothing to list', async () => {
    await render(<BarChart title="T" rows={[]} />);
    expect(screen.queryByRole('button', { name: 'Show as list' })).toBeNull();
  });
});

describe('criterion 5: phase 1 data without games or low_sample', () => {
  it('renders a mana-curve bar chart with no sample wording anywhere', async () => {
    await render(<BarChart title="Mana curve" rows={manaCurve} />);
    expect(screen.getByTestId('victory-chart')).toBeTruthy();
    expect(xLabels()).toEqual(['1', '2', '3', '4']);
    expect(screen.getByRole('image').props.accessibilityLabel).toBe(
      'Mana curve. Top values: 2 12, 3 9, 1 7.',
    );
    expect(screen.queryByText(/low sample/i)).toBeNull();
    expect(screen.queryByText(/\bgames?\b/i)).toBeNull();
    expect(screen.queryByText(/\*/)).toBeNull();
  });

  it('draws every mana-curve bar solid', async () => {
    await render(<BarChart title="Mana curve" rows={manaCurve} />);
    const [solid, faded] = screen.getAllByTestId('victory-bar');
    expect(solid?.props.count).toBe(4);
    expect(faded?.props.count).toBe(0);
  });

  it('lists mana-curve rows as label and value only', async () => {
    await render(<BarChart title="Mana curve" rows={manaCurve} />);
    await fireEvent.press(screen.getByRole('button', { name: 'Show as list' }));
    const list = screen.getByTestId('chart-list');
    expect(
      within(list)
        .getAllByRole('text')
        .map((t) => t.props.children),
    ).toEqual(['1: 7', '2: 12', '3: 9', '4: 4']);
  });

  it('shows only the value when a mana-curve bar is tapped', async () => {
    mockState.pressed = 1;
    await render(<BarChart title="Mana curve" rows={manaCurve} />);
    expect(childrenOf('chart-tooltip')).toBe('2: 12');
  });

  it('renders a color-share stacked chart with no sample wording anywhere', async () => {
    await render(<StackedBarChart title="Color share" series={series} rows={colorShare} />);
    expect(screen.getByTestId('victory-chart')).toBeTruthy();
    const columns = screen.getAllByTestId('stacked-column');
    expect(columns).toHaveLength(2);
    for (const c of columns) expect(c.props.opacity ?? 1).toBe(1);
    expect(screen.queryByText(/low sample/i)).toBeNull();
    expect(screen.queryByText(/\bgames?\b/i)).toBeNull();
    await fireEvent.press(screen.getByRole('button', { name: 'Show as list' }));
    const list = screen.getByTestId('chart-list');
    expect(
      within(list)
        .getAllByRole('text')
        .map((t) => t.props.children),
    ).toEqual(['Creatures: Red 33%, Blue 17%, Green 50%', 'Spells: Red 17%, Blue 83%, Green 0%']);
  });

  it('draws the line chart too when rows carry no sample info', async () => {
    await render(<LineChart title="Trend" rows={manaCurve} />);
    expect(screen.queryByText(/low sample/i)).toBeNull();
    expect(screen.queryByText(/\bgames?\b/i)).toBeNull();
  });

  it('accepts a stats API row (games and low_sample set) as it is', async () => {
    const apiRow = { label: 'A', value: 1, games: 5, low_sample: false };
    await render(<BarChart title="T" rows={[apiRow]} />);
    expect(screen.getByTestId('victory-chart')).toBeTruthy();
  });
});

describe('criterion 3: the list view is announced as a list', () => {
  it('gives the list container the list role and a label naming the chart', async () => {
    await render(charts.bar());
    await fireEvent.press(screen.getByRole('button', { name: 'Show as list' }));
    const list = screen.getByTestId('chart-list');
    expect(list.props.accessibilityRole).toBe('list');
    expect(list.props.accessibilityLabel).toContain('Win rate');
  });
});
