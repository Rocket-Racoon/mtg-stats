import { act, fireEvent, render, screen } from '@testing-library/react-native';
import { GestureHandlerRootView, State } from 'react-native-gesture-handler';
import { fireGestureHandler, getByGestureTestId } from 'react-native-gesture-handler/jest-utils';
import { getAnimatedStyle, setUpTests } from 'react-native-reanimated';
// Under Reanimated's Jest resolver Worklets runs its web build, whose getUIRuntimeHolder throws when
// Gesture Handler's deferred UI-runtime bootstrap calls it. There is no UI runtime in Jest.
jest.mock(
  'react-native-gesture-handler/lib/module/handlers/gestures/installUIRuntimeBindings',
  () => ({
    installUIRuntimeBindings: jest.fn(),
  }),
);

import { GestureTestScreen, MOVE_STEP } from '../components/GestureTestScreen';

function translation(): { x: number; y: number } {
  // Reanimated's typings declare getAnimatedStyle as returning void; at runtime it returns the style.
  const read = getAnimatedStyle as unknown as (component: unknown) => {
    transform?: Array<Record<string, number>>;
  };
  const style = read(screen.getByTestId('gesture-box'));
  let x = 0;
  let y = 0;
  for (const t of style.transform ?? []) {
    if ('translateX' in t) x = t.translateX;
    if ('translateY' in t) y = t.translateY;
  }
  return { x, y };
}

setUpTests();

async function renderScreen() {
  await render(
    <GestureHandlerRootView>
      <GestureTestScreen />
    </GestureHandlerRootView>,
  );
}

describe('GestureTestScreen (AC3 logic layer)', () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });
  afterEach(() => {
    jest.useRealTimers();
  });

  it('renders the draggable box and an accessible Move button', async () => {
    await renderScreen();
    expect(screen.getByLabelText('Draggable box')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Move box' })).toBeTruthy();
    expect(screen.getByText('Move')).toBeTruthy();
  });

  it('starts the box at the origin', async () => {
    await renderScreen();
    expect(translation()).toEqual({ x: 0, y: 0 });
  });

  it('moves the box by the step when Move is pressed', async () => {
    await renderScreen();
    await fireEvent.press(screen.getByRole('button', { name: 'Move box' }));
    await act(async () => {
      jest.advanceTimersByTime(1000);
    });
    expect(translation()).toEqual({ x: MOVE_STEP, y: MOVE_STEP });
  });

  it('moves cumulatively on repeated Move presses', async () => {
    await renderScreen();
    const button = screen.getByRole('button', { name: 'Move box' });
    await fireEvent.press(button);
    await act(async () => {
      jest.advanceTimersByTime(1000);
    });
    await fireEvent.press(button);
    await act(async () => {
      jest.advanceTimersByTime(1000);
    });
    expect(translation()).toEqual({ x: 2 * MOVE_STEP, y: 2 * MOVE_STEP });
  });

  async function drag(translationX: number, translationY: number) {
    await act(async () => {
      fireGestureHandler(getByGestureTestId('gesture-box-pan'), [
        { state: State.BEGAN },
        { state: State.ACTIVE, translationX: 0, translationY: 0 },
        // Same state as the previous event, so it is delivered as onUpdate, not onStart.
        { state: State.ACTIVE, translationX, translationY },
        { state: State.END, translationX, translationY },
      ]);
      jest.advanceTimersByTime(100);
    });
  }

  it('drags the box by the pan translation', async () => {
    await renderScreen();
    await drag(30, 20);
    expect(translation()).toEqual({ x: 30, y: 20 });
  });

  it('moves the same box by tap after a drag, composing both movements', async () => {
    await renderScreen();
    await drag(30, 20);
    await fireEvent.press(screen.getByRole('button', { name: 'Move box' }));
    await act(async () => {
      jest.advanceTimersByTime(1000);
    });
    expect(translation()).toEqual({ x: 30 + MOVE_STEP, y: 20 + MOVE_STEP });
  });

  it('continues a second drag from where the box was left', async () => {
    await renderScreen();
    await drag(30, 20);
    await drag(10, 5);
    expect(translation()).toEqual({ x: 40, y: 25 });
  });

  // MANUAL (no emulator available): drag and release the box on iOS and Android without errors.
  it.todo('MANUAL: box can be dragged and released on iOS and Android without errors');
});
