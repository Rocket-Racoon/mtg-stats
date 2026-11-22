import { DashPathEffect, Rect } from '@shopify/react-native-skia';

interface Props {
  x: number;
  y: number;
  width: number;
  height: number;
  color: string;
}

/**
 * Dashed outline around a faded small-sample mark. The fill is faded, so the outline (the theme's
 * border color, at least 3:1 against the background) keeps the shape visible without relying on
 * color or on the faded fill.
 */
export function SmallSampleOutline({ x, y, width, height, color }: Props) {
  if (![x, y, width, height].every(Number.isFinite) || height <= 0) return null;
  return (
    <Rect x={x} y={y} width={width} height={height} color={color} style="stroke" strokeWidth={2}>
      <DashPathEffect intervals={[5, 4]} />
    </Rect>
  );
}
