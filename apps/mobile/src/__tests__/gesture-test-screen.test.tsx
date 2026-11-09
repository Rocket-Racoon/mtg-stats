// Jest runs Reanimated with synchronous shared values, so these tests cover the component's logic,
// not UI-thread ordering. Thread ordering is verified on a device.
import { act, fireEvent, render, screen } from '@testing-library/react-native';
import { GestureHandlerRootView, State } from 'react-native-gesture-handler';
import {
  createGestureController,
  fireGestureHandler,
  getByGestureTestId,
} from 'react-native-gesture-handler/jest-utils';
import { getAnimatedStyle, setUpTests } from 'react-native-reanimated';
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

  async function pressMove() {
    await fireEvent.press(screen.getByRole('button', { name: 'Move box' }));
  }

  async function advance(ms: number) {
    await act(async () => {
      jest.advanceTimersByTime(ms);
    });
  }

  it('accumulates rapid taps made without advancing timers in between', async () => {
    await renderScreen();
    await pressMove();
    await pressMove();
    await advance(1000);
    expect(translation()).toEqual({ x: 2 * MOVE_STEP, y: 2 * MOVE_STEP });
  });

  it('accumulates a second tap made while the first animation is half done', async () => {
    await renderScreen();
    await pressMove();
    await advance(150);
    await pressMove();
    await advance(1000);
    expect(translation()).toEqual({ x: 2 * MOVE_STEP, y: 2 * MOVE_STEP });
  });

  it('commits the drop position so a following Move adds to it', async () => {
    await renderScreen();
    await drag(30, 20);
    await advance(1000);
    expect(translation()).toEqual({ x: 30, y: 20 });
    await pressMove();
    await advance(1000);
    expect(translation()).toEqual({ x: 30 + MOVE_STEP, y: 20 + MOVE_STEP });
  });

  it('moves the box by the same step via the activate accessibility action', async () => {
    await renderScreen();
    const box = screen.getByTestId('gesture-box');
    expect(box.props.accessible).toBe(true);
    await fireEvent(box, 'accessibilityAction', { nativeEvent: { actionName: 'activate' } });
    await advance(1000);
    expect(translation()).toEqual({ x: MOVE_STEP, y: MOVE_STEP });
  });

  // fireGestureHandler always appends an END event, so a drag held open needs the controller.
  function pan() {
    return createGestureController('gesture-box-pan');
  }

  it('ignores a Move tap made during an active drag, leaving only the drop position', async () => {
    await renderScreen();
    const gesture = pan();
    await act(async () => {
      gesture.begin();
      gesture.activate({ translationX: 0, translationY: 0 });
      gesture.update({ translationX: 30, translationY: 20 });
      jest.advanceTimersByTime(100);
    });
    await pressMove();
    await advance(1000);
    expect(translation()).toEqual({ x: 30, y: 20 });
    await act(async () => {
      gesture.end({ translationX: 30, translationY: 20 });
      jest.advanceTimersByTime(100);
    });
    await advance(1000);
    expect(translation()).toEqual({ x: 30, y: 20 });
  });

  it('snaps back to the previous target when the drag is cancelled, committing nothing', async () => {
    await renderScreen();
    await pressMove();
    // Start the drag mid-animation: the snap-back must land on the committed target (MOVE_STEP),
    // not on where the drag began.
    await advance(150);
    const gesture = pan();
    await act(async () => {
      gesture.begin();
      gesture.activate({ translationX: 0, translationY: 0 });
      gesture.update({ translationX: 30, translationY: 20 });
      gesture.cancel({ translationX: 30, translationY: 20 });
      jest.advanceTimersByTime(100);
    });
    await advance(1000);
    expect(translation()).toEqual({ x: MOVE_STEP, y: MOVE_STEP });
    // Nothing was committed: the next Move adds to the old target.
    await pressMove();
    await advance(1000);
    expect(translation()).toEqual({ x: 2 * MOVE_STEP, y: 2 * MOVE_STEP });
  });

  it('starts a drag from the position shown mid-animation', async () => {
    await renderScreen();
    await pressMove();
    await advance(150);
    const shown = translation();
    expect(shown.x).toBeGreaterThan(0);
    expect(shown.x).toBeLessThan(MOVE_STEP);
    await drag(30, 20);
    await advance(1000);
    expect(translation().x).toBeCloseTo(shown.x + 30, 5);
    expect(translation().y).toBeCloseTo(shown.y + 20, 5);
  });

  // MANUAL: Android is required (emulator or device), iOS is optional (decisions log, #40).
  it.todo('MANUAL: box can be dragged and released on Android (required) and iOS (optional)');
});
