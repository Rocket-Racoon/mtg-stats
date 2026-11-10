import { Pressable, StyleSheet, Text } from 'react-native';

/** Minimum touch target in pt (S-1.7.2 accessibility criterion). */
export const MIN_TOUCH_TARGET = 44;

type Props = {
  label: string;
  onPress: () => void;
  testID?: string;
};

/** Button whose visible text doubles as its accessibility label, with a >= 44 x 44 pt target. */
export function AppButton({ label, onPress, testID }: Props) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      onPress={onPress}
      testID={testID}
      style={styles.button}
    >
      <Text style={styles.text}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  button: {
    minHeight: MIN_TOUCH_TARGET,
    minWidth: MIN_TOUCH_TARGET,
    paddingHorizontal: 16,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 8,
    backgroundColor: '#1f2937',
  },
  text: { color: '#ffffff', fontSize: 16, fontWeight: '600' },
});
