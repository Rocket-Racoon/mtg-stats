import { fireEvent, render, screen } from '@testing-library/react-native';
import { AccessibilityInfo, StyleSheet } from 'react-native';
import './chart-mocks';
import { failedTouch, tapMark, tapOutside } from './chart-tap';
import { BarChart, LineChart, StackedBarChart } from '../components/charts';
import { axisLabel, displayShares } from '../components/charts/format';
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

const tooltip = () => screen.getByTestId('chart-tooltip', { includeHiddenElements: true });

describe('chart summary label', () => {
  it('announces the title and the top three values, flagging a small sample', async () => {
    await render(<BarChart title="Win rate by color" rows={rows} />);
    expect(
      screen.getByLabelText(
        'Win rate by color. Top values: Red 60, Blue 55 (small sample), Green 40.',
      ),
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
    expect(screen.getByText('Blue: 55, Small sample: 3 games')).toBeTruthy();
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

  it('says "1 game" for a single game', async () => {
    await render(
      <BarChart title="T" rows={[{ label: 'A', value: 5, games: 1, low_sample: true }]} />,
    );
    await fireEvent.press(screen.getByLabelText('Show as list'));
    expect(screen.getByText('A: 5, Small sample: 1 game')).toBeTruthy();
  });
});

describe('small-sample styling', () => {
  it('draws small-sample bars faded and dashed-outlined, solid bars solid', async () => {
    await render(<BarChart title="T" rows={rows} />);
    const bars = screen.getAllByTestId('victory-bar');
    expect(bars[0]?.props.count).toBe(3);
    expect(bars[0]?.props.opacity).toBeUndefined();
    expect(bars[1]?.props.count).toBe(1);
    expect(bars[1]?.props.opacity).toBeLessThan(0.5);
    const outlines = screen.getAllByTestId('skia-rect');
    expect(outlines).toHaveLength(1);
    expect(outlines[0]?.props.style).toBe('stroke');
    expect(screen.getByText(/Small sample: faded and outlined/)).toBeTruthy();
  });

  it('draws small-sample line points as small hollow rings', async () => {
    await render(<LineChart title="T" rows={rows} />);
    const dots = screen.getAllByTestId('victory-scatter');
    expect(dots[0]?.props.radius).toBeGreaterThan(dots[1]?.props.radius as number);
    expect(dots[1]?.props.paintStyle).toBe('stroke');
    expect(dots[1]?.props.opacity).toBeLessThan(1);
  });

  it('outlines a faded small-sample stacked column', async () => {
    await render(
      <StackedBarChart
        title="T"
        series={colorSeries}
        rows={[
          { label: 'A', segments: { r: 1, u: 1 } },
          { label: 'B', segments: { r: 1, u: 1 }, low_sample: true },
        ]}
      />,
    );
    expect(screen.getAllByTestId('skia-rect')).toHaveLength(1);
  });

  it('shows no small-sample note when rows carry no sample info', async () => {
    await render(<BarChart title="T" rows={[{ label: 'a', value: 1 }]} />);
    expect(screen.queryByText(/Small sample/)).toBeNull();
    expect(screen.queryByTestId('skia-rect')).toBeNull();
  });
});

describe('tap selection lifecycle', () => {
  it('shows nothing until a mark is tapped, and hides the placeholder from screen readers', async () => {
    await render(<BarChart title="T" rows={rows} />);
    expect(tooltip().props.children).toBe(' ');
    expect(tooltip().props.accessibilityElementsHidden).toBe(true);
    expect(tooltip().props.importantForAccessibility).toBe('no-hide-descendants');
  });

  it('keeps the tooltip after the finger lifts, and exposes it to screen readers', async () => {
    await render(<BarChart title="T" rows={rows} />);
    await tapMark(1);
    // tapMark ends with the gesture END event: the finger is already up.
    expect(tooltip().props.children).toBe('Blue: 55, Small sample: 3 games');
    expect(tooltip().props.accessibilityElementsHidden).toBe(false);
    expect(tooltip().props.importantForAccessibility).toBe('auto');
  });

  it('moves the selection to another mark, then clears it on a tap of the same mark', async () => {
    await render(<BarChart title="T" rows={rows} />);
    await tapMark(1);
    await tapMark(2);
    expect(tooltip().props.children).toBe('Green: 40, 12 games');
    await tapMark(2);
    expect(tooltip().props.children).toBe(' ');
  });

  it('reports each selection and the clearing through onSelect', async () => {
    const onSelect = jest.fn();
    await render(<BarChart title="T" rows={rows} onSelect={onSelect} />);
    await tapMark(0);
    await tapMark(3);
    await tapMark(3);
    expect(onSelect.mock.calls).toEqual([[0], [3], [null]]);
  });

  it('clears the selection when switching to the list', async () => {
    const onSelect = jest.fn();
    await render(<BarChart title="T" rows={rows} onSelect={onSelect} />);
    await tapMark(1);
    await fireEvent.press(screen.getByLabelText('Show as list'));
    expect(onSelect).toHaveBeenLastCalledWith(null);
    await fireEvent.press(screen.getByLabelText('Show as chart'));
    expect(tooltip().props.children).toBe(' ');
  });

  it('ignores a touch that fails (a scroll) and a tap outside the plot area', async () => {
    const onSelect = jest.fn();
    await render(<BarChart title="T" rows={rows} onSelect={onSelect} />);
    await failedTouch(1);
    await tapOutside();
    expect(tooltip().props.children).toBe(' ');
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('announces a selection for screen readers on both platforms', async () => {
    const announce = jest.spyOn(AccessibilityInfo, 'announceForAccessibility');
    await render(<BarChart title="T" rows={rows} />);
    await tapMark(0);
    expect(announce).toHaveBeenCalledWith('Red: 60, 20 games');
    announce.mockRestore();
  });

  it('selects line points and stacked columns the same way', async () => {
    await render(<LineChart title="T" rows={[{ label: 'a', value: 2.5 }]} />);
    await tapMark(0);
    expect(tooltip().props.children).toBe('a: 2.5');
    await screen.unmount();
    await render(
      <StackedBarChart
        title="T"
        series={colorSeries}
        rows={[{ label: 'Deck', segments: { r: 1, u: 3 }, games: 4 }]}
      />,
    );
    await tapMark(0);
    expect(tooltip().props.children).toBe('Deck: Red 25%, Blue 75%, 4 games');
  });
});

describe('x axis labels', () => {
  const curve = Array.from({ length: 8 }, (_, i) => ({
    label: String(i),
    value: i + 1,
    low_sample: i === 5,
  }));

  it.each([
    ['bar', (r: typeof curve) => <BarChart title="T" rows={r} />],
    ['line', (r: typeof curve) => <LineChart title="T" rows={r} />],
  ])(
    'keeps every label of an 8-row %s chart, including the small-sample star',
    async (_n, make) => {
      await render(make(curve));
      expect(screen.getAllByTestId('x-label').map((l) => l.props.children)).toEqual([
        '0',
        '1',
        '2',
        '3',
        '4',
        '5*',
        '6',
        '7',
      ]);
    },
  );

  it('keeps every label of a stacked chart with 8 rows', async () => {
    await render(
      <StackedBarChart
        title="T"
        series={colorSeries}
        rows={curve.map((c) => ({ label: c.label, segments: { r: 1, u: 1 } }))}
      />,
    );
    expect(screen.getAllByTestId('x-label')).toHaveLength(8);
  });

  it('shortens long labels but keeps the full label in the list, tooltip and summary', async () => {
    const long = Array.from({ length: 8 }, (_, i) => ({
      label: `Commander number ${i}`,
      value: 10 - i,
      low_sample: i === 0,
    }));
    await render(<BarChart title="T" rows={long} />);
    const labels = screen.getAllByTestId('x-label').map((l) => l.props.children as string);
    expect(labels[0]).toBe('Comm…*');
    expect(labels).toHaveLength(8);
    expect(screen.getByLabelText(/Commander number 0 10 \(small sample\)/)).toBeTruthy();
    await tapMark(1);
    expect(tooltip().props.children).toBe('Commander number 1: 9');
    await fireEvent.press(screen.getByLabelText('Show as list'));
    expect(screen.getByText('Commander number 7: 3')).toBeTruthy();
  });

  it('axisLabel never cuts off the star', () => {
    expect(axisLabel('Abcdefghijkl', true, 20)).toBe('Ab…*');
    expect(axisLabel('Ab', false, 20)).toBe('Ab');
  });
});

describe('legend and non-color cues', () => {
  it('shows a swatch row naming every stacked series, hidden from screen readers', async () => {
    await render(
      <StackedBarChart
        title="T"
        series={colorSeries}
        rows={[{ label: 'A', segments: { r: 1, u: 1 } }]}
      />,
    );
    expect(screen.getByText(/Segments, bottom to top: Red, Blue/)).toBeTruthy();
    expect(screen.getByText('Red', { includeHiddenElements: true })).toBeTruthy();
    expect(screen.queryByText('Red')).toBeNull();
  });

  it('gives the list toggle a visible outline', async () => {
    await render(<BarChart title="T" rows={rows} />);
    const toggle = screen.getByTestId('chart-list-toggle');
    expect(StyleSheet.flatten(toggle.parent?.props.style)).toMatchObject({
      borderWidth: 2,
      borderColor: '#6b7280',
    });
  });
});

describe('edge cases', () => {
  it('treats a NaN value as no value: not drawn, listed as "no value", ignored by the summary', async () => {
    await render(
      <BarChart
        title="T"
        rows={[
          { label: 'A', value: Number.NaN },
          { label: 'B', value: 4 },
        ]}
      />,
    );
    expect(screen.getAllByTestId('victory-bar')[0]?.props.count).toBe(1);
    expect(screen.getByLabelText('T. Top values: B 4.')).toBeTruthy();
    await fireEvent.press(screen.getByLabelText('Show as list'));
    expect(screen.getByText('A: no value')).toBeTruthy();
  });

  it('shows the empty message when no row has a finite value', async () => {
    await render(<LineChart title="T" rows={[{ label: 'A', value: Number.POSITIVE_INFINITY }]} />);
    expect(screen.getByTestId('chart-empty')).toBeTruthy();
  });

  it('draws a negative value as zero height but reports its real value', async () => {
    await render(
      <BarChart
        title="T"
        rows={[
          { label: 'A', value: -3 },
          { label: 'B', value: 2 },
        ]}
      />,
    );
    await tapMark(0);
    expect(tooltip().props.children).toBe('A: -3');
  });

  it('draws a negative reference line at zero and a non-finite one not at all', async () => {
    await render(<BarChart title="T" rows={rows} referenceLine={{ value: -5, label: 'Floor' }} />);
    expect(screen.getByTestId('skia-line').props.p1.y).toBe(100);
    await screen.unmount();
    await render(
      <BarChart title="T" rows={rows} referenceLine={{ value: Number.NaN, label: 'Bad' }} />,
    );
    expect(screen.queryByTestId('skia-line')).toBeNull();
    expect(screen.queryByText(/Bad/)).toBeNull();
  });

  it('survives a series named "index" and keeps its own column data', async () => {
    await render(
      <StackedBarChart
        title="T"
        series={[
          { key: 'index', label: 'Idx' },
          { key: 'x', label: 'Ex' },
        ]}
        rows={[
          { label: 'A', segments: { index: 3, x: 1 } },
          { label: 'B', segments: { index: 1, x: 1 } },
        ]}
      />,
    );
    expect(screen.getAllByTestId('x-label').map((l) => l.props.children)).toEqual(['A', 'B']);
    await tapMark(1);
    expect(tooltip().props.children).toBe('B: Idx 50%, Ex 50%');
  });

  it('shows the empty message when every stacked row totals zero', async () => {
    await render(
      <StackedBarChart
        title="T"
        series={colorSeries}
        rows={[
          { label: 'A', segments: { r: 0, u: 0 } },
          { label: 'B', segments: {} },
        ]}
      />,
    );
    expect(screen.getByTestId('chart-empty')).toBeTruthy();
    expect(screen.queryByTestId('victory-chart')).toBeNull();
  });

  it('makes displayed shares add up to 100', () => {
    const series = [
      { key: 'a', label: 'A' },
      { key: 'b', label: 'B' },
      { key: 'c', label: 'C' },
    ];
    const shares = displayShares({ label: 'x', segments: { a: 1, b: 1, c: 1 } }, series);
    expect(Object.values(shares).reduce((a, b) => a + b, 0)).toBe(100);
    const odd = displayShares({ label: 'x', segments: { a: 2, b: 1, c: 0 } }, series);
    expect(odd).toEqual({ a: 67, b: 33, c: 0 });
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
  it('turns segments into exact percentages and survives a zero total', () => {
    const series = [
      { key: 'a', label: 'A' },
      { key: 'b', label: 'B' },
    ];
    expect(toShares({ label: 'x', segments: { a: 1, b: 3 } }, series)).toEqual({ a: 25, b: 75 });
    expect(toShares({ label: 'x', segments: {} }, series)).toEqual({ a: 0, b: 0 });
  });
});
