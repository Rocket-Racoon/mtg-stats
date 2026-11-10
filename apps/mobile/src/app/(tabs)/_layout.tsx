import { Tabs } from 'expo-router/js-tabs';
import { MIN_TOUCH_TARGET } from '../../components/AppButton';

const tab = (title: string) => ({
  title,
  headerShown: false,
  tabBarAccessibilityLabel: title,
  tabBarButtonTestID: `tab-${title.toLowerCase()}`,
});

export default function TabsLayout() {
  return (
    // backBehavior "firstRoute": hardware back on Decks, Stats or More returns to Play; on Play it exits.
    <Tabs
      backBehavior="firstRoute"
      screenOptions={{
        tabBarItemStyle: { minHeight: MIN_TOUCH_TARGET, minWidth: MIN_TOUCH_TARGET },
        tabBarLabelStyle: { fontSize: 14 },
      }}
    >
      <Tabs.Screen name="play" options={tab('Play')} />
      <Tabs.Screen name="decks" options={tab('Decks')} />
      <Tabs.Screen name="stats" options={tab('Stats')} />
      <Tabs.Screen name="more" options={tab('More')} />
    </Tabs>
  );
}
