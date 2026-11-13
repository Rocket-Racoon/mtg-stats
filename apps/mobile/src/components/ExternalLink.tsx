import { Linking, Pressable, StyleSheet, Text } from 'react-native';
import { MIN_TOUCH_TARGET } from './AppButton';

type Props = {
  label: string;
  href: string;
  /** Inline: nested inside a paragraph `Text`, so the link flows with the sentence. */
  inline?: boolean;
  testID?: string;
};

/** Opens an https URL in the system browser (`Linking.openURL`), announced as a link. */
export function ExternalLink({ label, href, inline, testID }: Props) {
  const open = () => {
    // A failed hand-off (no browser installed) must not crash the screen.
    Linking.openURL(href).catch(() => undefined);
  };
  if (inline) {
    return (
      <Text
        accessibilityRole="link"
        accessibilityLabel={label}
        accessibilityHint="Opens in your browser"
        onPress={open}
        testID={testID}
        style={styles.link}
      >
        {label}
      </Text>
    );
  }
  return (
    <Pressable
      accessibilityRole="link"
      accessibilityLabel={label}
      accessibilityHint="Opens in your browser"
      onPress={open}
      testID={testID}
      style={styles.target}
    >
      <Text style={styles.link}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  link: { color: '#1d4ed8', textDecorationLine: 'underline', fontSize: 16 },
  target: { minHeight: MIN_TOUCH_TARGET, minWidth: MIN_TOUCH_TARGET, justifyContent: 'center' },
});
