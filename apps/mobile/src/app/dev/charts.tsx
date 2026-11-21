import { Redirect } from 'expo-router';
import { ChartShowcase } from '../../components/charts/ChartShowcase';

/** Dev-only route: hidden in production builds. */
export default function ChartsRoute() {
  if (!__DEV__) return <Redirect href="/" />;
  return <ChartShowcase />;
}
