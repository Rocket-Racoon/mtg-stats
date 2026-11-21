import { useState, type ReactNode } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { AppButton } from '../AppButton';
import { DEFAULT_EMPTY_MESSAGE } from './format';
import { useChartTheme } from './theme';
import type { ChartCommonProps } from './types';

export interface ListItem {
  key: string;
  /** The full text of the list entry, read as one phrase. */
  text: string;
}

interface Props extends ChartCommonProps {
  /** Screen-reader summary of the chart (title and top values). */
  summary: string;
  isEmpty: boolean;
  items: readonly ListItem[];
  /** Tooltip text for the pressed bar or point; null when nothing is pressed. */
  tooltip: string | null;
  /** Explains the low-sample marker; rendered only when some row is low-sample. */
  hasLowSample: boolean;
  /** Plain-text legend lines (series names, reference line). Text, so color is never the only cue. */
  legend?: readonly string[];
  children: ReactNode;
}

/**
 * Shared shell of the kit's charts: title, empty state, the chart with its summary label, the
 * tap tooltip, the low-sample note and the "Show as list" toggle with its accessible list.
 */
export function ChartFrame({
  title,
  summary,
  isEmpty,
  emptyMessage = DEFAULT_EMPTY_MESSAGE,
  items,
  tooltip,
  hasLowSample,
  legend = [],
  height = 220,
  testID,
  children,
}: Props) {
  const theme = useChartTheme();
  const [showList, setShowList] = useState(false);

  return (
    <View testID={testID} style={[styles.frame, { backgroundColor: theme.background }]}>
      <Text accessibilityRole="header" style={[styles.title, { color: theme.text }]}>
        {title}
      </Text>
      {isEmpty ? (
        <Text testID="chart-empty" style={[styles.empty, { color: theme.mutedText }]}>
          {emptyMessage}
        </Text>
      ) : (
        <>
          {showList ? (
            <View accessibilityRole="list" accessibilityLabel={`${title} data`} testID="chart-list">
              {items.map((item) => (
                <Text
                  key={item.key}
                  accessible
                  accessibilityRole="text"
                  style={[styles.item, { color: theme.text, borderColor: theme.grid }]}
                >
                  {item.text}
                </Text>
              ))}
            </View>
          ) : (
            <>
              <View
                testID="chart-canvas"
                accessible
                accessibilityRole="image"
                accessibilityLabel={summary}
                style={{ height }}
              >
                {children}
              </View>
              {legend.map((line) => (
                <Text key={line} style={[styles.note, { color: theme.mutedText }]}>
                  {line}
                </Text>
              ))}
              <Text
                testID="chart-tooltip"
                accessibilityLiveRegion="polite"
                style={[
                  styles.tooltip,
                  tooltip
                    ? { backgroundColor: theme.tooltipBackground, color: theme.tooltipText }
                    : styles.tooltipHidden,
                ]}
              >
                {tooltip ?? ' '}
              </Text>
              {hasLowSample ? (
                <Text
                  testID="chart-low-sample-note"
                  style={[styles.note, { color: theme.mutedText }]}
                >
                  * Low sample: drawn faded, so read with care.
                </Text>
              ) : null}
            </>
          )}
          <AppButton
            testID="chart-list-toggle"
            label={showList ? 'Show as chart' : 'Show as list'}
            onPress={() => setShowList((v) => !v)}
          />
        </>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  frame: { gap: 8, padding: 12 },
  title: { fontSize: 16, fontWeight: '700' },
  empty: { fontSize: 14, paddingVertical: 24, textAlign: 'center' },
  item: { fontSize: 14, paddingVertical: 8, borderBottomWidth: StyleSheet.hairlineWidth },
  tooltip: {
    alignSelf: 'flex-start',
    borderRadius: 6,
    fontSize: 13,
    paddingHorizontal: 8,
    paddingVertical: 4,
  },
  tooltipHidden: { opacity: 0 },
  note: { fontSize: 12 },
});
