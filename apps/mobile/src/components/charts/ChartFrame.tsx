import { useEffect, useState, type ReactNode } from 'react';
import { AccessibilityInfo, StyleSheet, Text, View } from 'react-native';
import {
  GestureDetector,
  GestureHandlerRootView,
  type TapGesture,
} from 'react-native-gesture-handler';
import { AppButton } from '../AppButton';
import { DEFAULT_EMPTY_MESSAGE } from './format';
import { useChartTheme } from './theme';
import type { ChartCommonProps } from './types';

export interface ListItem {
  key: string;
  /** The full text of the list entry, read as one phrase. */
  text: string;
}

export interface LegendLine {
  text: string;
  /** A decorative swatch drawn before the text; the text carries the meaning. */
  swatch?: { color: string; dashed?: boolean };
}

export interface SeriesSwatch {
  label: string;
  color: string;
}

interface Props extends Omit<ChartCommonProps, 'onSelect'> {
  /** Screen-reader summary of the chart (title and top values). */
  summary: string;
  isEmpty: boolean;
  items: readonly ListItem[];
  /** Tooltip text for the selected bar or point; null when nothing is selected. */
  tooltip: string | null;
  /** Explains the small-sample marker; rendered only when some row is a small sample. */
  hasLowSample: boolean;
  /** Plain-text legend lines (series names, reference line). */
  legend?: readonly LegendLine[];
  /** Colored series keys for sighted users; hidden from screen readers (the legend text names them). */
  swatches?: readonly SeriesSwatch[];
  /** Tap gesture of the chart (see useTapSelection). */
  gesture?: TapGesture;
  /** Called when the user switches to the list, so the chart can drop its selection. */
  onShowList?: () => void;
  children: ReactNode;
}

/**
 * Shared shell of the kit's charts: title, empty state, the chart with its summary label, the
 * tap tooltip, the small-sample note and the "Show as list" toggle with its accessible list.
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
  swatches = [],
  gesture,
  onShowList,
  height = 220,
  testID,
  children,
}: Props) {
  const theme = useChartTheme();
  const [showList, setShowList] = useState(false);

  // A selection is announced on both platforms (accessibilityLiveRegion is Android-only).
  useEffect(() => {
    if (tooltip) AccessibilityInfo.announceForAccessibility(tooltip);
  }, [tooltip]);

  const canvas = (
    <View
      testID="chart-canvas"
      accessible
      accessibilityRole="image"
      accessibilityLabel={summary}
      style={{ height }}
    >
      {children}
    </View>
  );

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
              {gesture ? (
                // The kit carries its own root view, so it also works inside a modal or sheet.
                <GestureHandlerRootView>
                  <GestureDetector gesture={gesture}>{canvas}</GestureDetector>
                </GestureHandlerRootView>
              ) : (
                canvas
              )}
              {legend.map((line) => (
                <View key={line.text} style={styles.legendLine}>
                  {line.swatch ? (
                    <View
                      style={[
                        styles.lineSwatch,
                        {
                          borderColor: line.swatch.color,
                          borderStyle: line.swatch.dashed ? 'dashed' : 'solid',
                        },
                      ]}
                    />
                  ) : null}
                  <Text style={[styles.note, { color: theme.mutedText }]}>{line.text}</Text>
                </View>
              ))}
              {swatches.length > 0 ? (
                <View
                  style={styles.legendLine}
                  accessibilityElementsHidden
                  importantForAccessibility="no-hide-descendants"
                >
                  {swatches.map((s) => (
                    <View key={s.label} style={styles.legendLine}>
                      <View
                        style={[
                          styles.swatch,
                          { backgroundColor: s.color, borderColor: theme.border },
                        ]}
                      />
                      <Text style={[styles.note, { color: theme.text }]}>{s.label}</Text>
                    </View>
                  ))}
                </View>
              ) : null}
              <Text
                testID="chart-tooltip"
                // With nothing selected the placeholder only reserves the line: hide it from
                // TalkBack and VoiceOver both.
                accessibilityElementsHidden={!tooltip}
                importantForAccessibility={tooltip ? 'auto' : 'no-hide-descendants'}
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
                  * Small sample: faded and outlined, so read with care.
                </Text>
              ) : null}
            </>
          )}
          <View style={[styles.toggleOutline, { borderColor: theme.border }]}>
            <AppButton
              testID="chart-list-toggle"
              label={showList ? 'Show as chart' : 'Show as list'}
              onPress={() => {
                if (!showList) onShowList?.();
                setShowList((v) => !v);
              }}
            />
          </View>
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
  legendLine: { flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', gap: 6 },
  lineSwatch: { width: 24, borderTopWidth: 3 },
  swatch: { width: 14, height: 14, borderWidth: 1, borderRadius: 3 },
  // The toggle's own background can match the chart surface (dark scheme), so it gets an outline.
  toggleOutline: { alignSelf: 'flex-start', borderWidth: 2, borderRadius: 10 },
});
