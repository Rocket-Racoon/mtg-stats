import { act } from '@testing-library/react-native';
import { State } from 'react-native-gesture-handler';
import {
  createGestureController,
  fireGestureHandler,
  getByGestureTestId,
} from 'react-native-gesture-handler/jest-utils';

/** The chart mocks place mark `i` at canvas x = i (bounds 0 to 100), so a tap at x = i hits it. */
export async function tapMark(index: number): Promise<void> {
  await act(async () => {
    fireGestureHandler(getByGestureTestId('chart-tap'), [
      { state: State.BEGAN, x: index, y: 50 },
      { state: State.ACTIVE, x: index, y: 50 },
      { state: State.END, x: index, y: 50 },
    ]);
  });
}

/** A tap outside the plot area (past the right bound). */
export async function tapOutside(): Promise<void> {
  await act(async () => {
    fireGestureHandler(getByGestureTestId('chart-tap'), [
      { state: State.BEGAN, x: 500, y: 50 },
      { state: State.ACTIVE, x: 500, y: 50 },
      { state: State.END, x: 500, y: 50 },
    ]);
  });
}

/** A touch that starts and then fails (the finger moved: a scroll), as RNGH reports it. */
export async function failedTouch(index: number): Promise<void> {
  const controller = createGestureController('chart-tap');
  await act(async () => {
    controller.begin({ x: index, y: 50 });
    controller.fail();
  });
}
