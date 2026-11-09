import { Redirect } from 'expo-router';
import { GestureTestScreen } from '../../components/GestureTestScreen';

/** Dev-only route: hidden in production builds. */
export default function GestureTestRoute() {
  if (!__DEV__) return <Redirect href="/" />;
  return <GestureTestScreen />;
}
