import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ApiError, useMutation, useQueryClient } from '@mtg-stats/api-client';
import { createMemoryRouter } from 'react-router';
import { RouterProvider } from 'react-router/dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { GENERIC_ERROR_MESSAGE, NETWORK_ERROR_MESSAGE, errorMessage } from '../errors/message';
import { useSessionStore } from '../session/store';
import { Toaster } from '../ui/Toaster';
import { INFO_TOAST_MS, MAX_TOASTS, resetToasts, useToastStore } from '../ui/toasts';
import { AppProviders, createAppQueryClient } from './AppProviders';

function apiError(init: Partial<ConstructorParameters<typeof ApiError>[0]> = {}) {
  return new ApiError({ status: 422, code: 'validation_error', message: 'Name is taken', ...init });
}

function Boom({ error }: { error: unknown }): never {
  throw error;
}

// React logs caught render errors; hide only those, let any other console.error through.
function silenceExpectedErrorLogs(): void {
  const original = console.error.bind(console);
  vi.spyOn(console, 'error').mockImplementation((...args: unknown[]) => {
    const text = args.map((a) => (a instanceof Error ? a.message : String(a))).join(' ');
    if (/secret stack detail|Name is taken|The above error occurred|Error: Uncaught/.test(text)) {
      return;
    }
    original(...args);
  });
}

beforeEach(() => {
  silenceExpectedErrorLogs();
  resetToasts();
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('AppProviders (AC1)', () => {
  it('provides a QueryClient with the S-1.5.4 defaults', () => {
    function Probe() {
      const defaults = useQueryClient().getDefaultOptions();
      return (
        <p>
          stale {String(defaults.queries?.staleTime)} retry {String(defaults.mutations?.retry)}
        </p>
      );
    }
    render(
      <AppProviders>
        <Probe />
      </AppProviders>,
    );
    expect(screen.getByText('stale 30000 retry false')).toBeInTheDocument();
  });

  it('starts the session store signed out', () => {
    expect(useSessionStore.getState().status).toBe('signed_out');
  });
});

describe('error boundary (AC2)', () => {
  it('shows a fallback with a Reload button that reloads the page', async () => {
    const reload = vi.fn();
    vi.stubGlobal('location', { ...window.location, reload });
    render(
      <AppProviders>
        <Boom error={new Error('secret stack detail')} />
      </AppProviders>,
    );
    const heading = screen.getByRole('heading', { name: 'Something went wrong' });
    expect(heading).toHaveFocus();
    // The fallback is the page's only main landmark, named by its heading, with no alert role.
    expect(screen.getByRole('main', { name: 'Something went wrong' })).toContainElement(heading);
    expect(document.querySelectorAll('main, #main')).toHaveLength(1);
    expect(document.querySelector('.error-fallback')).not.toHaveAttribute('role');
    expect(screen.queryByText(/secret stack detail/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Request ID/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Reload' }));
    expect(reload).toHaveBeenCalledOnce();
  });

  it('shows the request id for an ApiError', () => {
    render(
      <AppProviders>
        <Boom error={apiError({ requestId: 'req-123' })} />
      </AppProviders>,
    );
    expect(screen.getByText('Request ID: req-123')).toBeInTheDocument();
    expect(screen.getByText('Name is taken')).toBeInTheDocument();
  });

  it('is also the root route errorElement', async () => {
    const { routes } = await import('../routes');
    const router = createMemoryRouter(
      [
        {
          errorElement: routes[0]?.errorElement,
          children: [{ path: '/', element: <Boom error={apiError({ requestId: 'r-9' })} /> }],
        },
      ],
      { initialEntries: ['/'] },
    );
    render(<RouterProvider router={router} />);
    expect(screen.getByRole('button', { name: 'Reload' })).toBeInTheDocument();
    expect(screen.getByText('Request ID: r-9')).toBeInTheDocument();
  });
});

describe('failed mutations (AC3)', () => {
  function Mutate({
    error,
    meta,
    onError,
  }: {
    error: unknown;
    meta?: { errorToast: boolean };
    onError?: () => void;
  }) {
    const m = useMutation({
      mutationFn: () => Promise.reject(error as Error),
      ...(meta ? { meta } : {}),
      ...(onError ? { onError } : {}),
    });
    return (
      <>
        <button
          type="button"
          onClick={() => {
            m.mutate();
          }}
        >
          Go
        </button>
        {m.isError && <p>failed</p>}
      </>
    );
  }

  it('toasts the envelope message, not JSON, and the toast is dismissible', async () => {
    render(
      <AppProviders>
        <Mutate error={apiError()} />
      </AppProviders>,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Go' }));
    const toast = await screen.findByText('Name is taken');
    expect(screen.getByRole('alert')).toContainElement(toast);
    expect(screen.getByRole('alert').textContent).not.toMatch(/[{}]/);
    await userEvent.click(screen.getByRole('button', { name: /Dismiss notification/ }));
    expect(screen.getByRole('alert')).toBeEmptyDOMElement();
  });

  it('shows a generic sentence for a non-ApiError', async () => {
    render(
      <AppProviders>
        <Mutate error={new TypeError('x is undefined')} />
      </AppProviders>,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Go' }));
    expect(await screen.findByText(GENERIC_ERROR_MESSAGE)).toBeInTheDocument();
    expect(screen.queryByText(/x is undefined/)).not.toBeInTheDocument();
  });

  it('does not toast when the mutation opts out, after the mutation has failed', async () => {
    render(
      <AppProviders>
        <Mutate error={apiError()} meta={{ errorToast: false }} />
      </AppProviders>,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Go' }));
    expect(await screen.findByText('failed')).toBeInTheDocument();
    expect(useToastStore.getState().toasts).toHaveLength(0);
    expect(screen.getByRole('alert')).toBeEmptyDOMElement();
  });

  it.each([
    ['with the toast', undefined],
    ['when opted out of the toast', { errorToast: false }],
  ])("still runs the mutation's own onError %s", async (_name, meta) => {
    const onError = vi.fn();
    render(
      <AppProviders>
        <Mutate error={apiError()} onError={onError} {...(meta ? { meta } : {})} />
      </AppProviders>,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Go' }));
    await waitFor(() => {
      expect(onError).toHaveBeenCalledOnce();
    });
    expect(useToastStore.getState().toasts).toHaveLength(meta ? 0 : 1);
  });

  it('createAppQueryClient keeps the defaults', () => {
    expect(createAppQueryClient().getDefaultOptions().queries?.staleTime).toBe(30_000);
  });
});

describe('errorMessage', () => {
  it('returns the envelope message for ApiError', () => {
    expect(errorMessage(apiError())).toBe('Name is taken');
  });

  it('returns a fixed sentence, not the browser text, for a network error', () => {
    expect(
      errorMessage(apiError({ status: 0, code: 'network_error', message: 'Failed to fetch' })),
    ).toBe(NETWORK_ERROR_MESSAGE);
    expect(NETWORK_ERROR_MESSAGE).toBe(
      "Can't reach the server. Check your connection and try again.",
    );
  });

  it.each([new Error('boom'), 'text', null, { error: 'raw' }, apiError({ message: '{"a":1}' })])(
    'returns the generic sentence for %j',
    (value) => {
      expect(errorMessage(value)).toBe(GENERIC_ERROR_MESSAGE);
    },
  );
});

describe('Toaster', () => {
  const push = (kind: 'info' | 'error', message: string) => {
    let id = 0;
    act(() => {
      id = useToastStore.getState().push(kind, message);
    });
    return id;
  };
  const count = () => useToastStore.getState().toasts.length;

  it('keeps a status and an alert live region in the DOM with no toasts', () => {
    render(<Toaster />);
    expect(screen.getByRole('region', { name: 'Notifications' })).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveAttribute('aria-live', 'polite');
    expect(screen.getByRole('alert')).toHaveAttribute('aria-live', 'assertive');
    expect(screen.getByRole('status')).toBeEmptyDOMElement();
    expect(screen.getByRole('alert')).toBeEmptyDOMElement();
  });

  it('puts info toasts in the status region and error toasts in the alert region', () => {
    render(<Toaster />);
    push('info', 'Saved');
    push('error', 'Nope');
    expect(screen.getByRole('status')).toHaveTextContent('Saved');
    expect(screen.getByRole('status')).not.toHaveTextContent('Nope');
    expect(screen.getByRole('alert')).toHaveTextContent('Nope');
  });

  it('auto-dismisses info toasts', () => {
    vi.useFakeTimers();
    render(<Toaster />);
    push('info', 'Saved');
    act(() => {
      vi.advanceTimersByTime(INFO_TOAST_MS);
    });
    expect(count()).toBe(0);
    expect(screen.getByRole('status')).toBeEmptyDOMElement();
  });

  it('keeps error toasts until dismissed', () => {
    vi.useFakeTimers();
    render(<Toaster />);
    push('error', 'Nope');
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(screen.getByRole('alert')).toHaveTextContent('Nope');
  });

  it('caps visible toasts and drops the oldest', () => {
    render(<Toaster />);
    for (let i = 1; i <= MAX_TOASTS + 2; i++) push('error', `Error ${String(i)}`);
    expect(count()).toBe(MAX_TOASTS);
    expect(screen.queryByText('Error 1')).not.toBeInTheDocument();
    expect(screen.queryByText('Error 2')).not.toBeInTheDocument();
    expect(screen.getByText('Error 5')).toBeInTheDocument();
  });

  it('drops the oldest info toast first at the cap, and an error only when all are errors', () => {
    render(<Toaster />);
    push('error', 'E1');
    push('info', 'I1');
    push('info', 'I2');
    push('error', 'E2');
    expect(screen.queryByText('I1')).not.toBeInTheDocument();
    expect(screen.getByText('I2')).toBeInTheDocument();
    expect(screen.getByText('E1')).toBeInTheDocument();
    push('error', 'E3');
    expect(screen.queryByText('I2')).not.toBeInTheDocument();
    expect(count()).toBe(MAX_TOASTS);
    push('error', 'E4');
    expect(screen.queryByText('E1')).not.toBeInTheDocument();
    expect(screen.getByText('E4')).toBeInTheDocument();
  });

  it('keeps the timer paused until both hover and focus end', () => {
    vi.useFakeTimers();
    render(<Toaster />);
    push('info', 'Saved');
    const toast = screen.getByText('Saved').closest('.toast');
    if (!toast) throw new Error('toast missing');
    const button = screen.getByRole('button', { name: /Dismiss notification: Saved/ });
    act(() => {
      fireEvent.mouseEnter(toast);
      button.focus();
    });
    act(() => {
      fireEvent.mouseLeave(toast);
      vi.advanceTimersByTime(60_000);
    });
    expect(count()).toBe(1); // still focused
    act(() => {
      button.blur();
      vi.advanceTimersByTime(INFO_TOAST_MS - 1);
    });
    expect(count()).toBe(1);
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(count()).toBe(0);

    push('info', 'Again');
    const again = screen.getByText('Again').closest('.toast');
    const againButton = screen.getByRole('button', { name: /Dismiss notification: Again/ });
    if (!again) throw new Error('toast missing');
    act(() => {
      againButton.focus();
      fireEvent.mouseEnter(again);
    });
    act(() => {
      againButton.blur();
      vi.advanceTimersByTime(60_000);
    });
    expect(count()).toBe(1); // still hovered
    act(() => {
      fireEvent.mouseLeave(again);
      vi.advanceTimersByTime(INFO_TOAST_MS);
    });
    expect(count()).toBe(0);
  });

  it('restores focus to #main when the focused toast is removed from outside the toaster', () => {
    vi.useFakeTimers();
    render(
      <>
        <main id="main" tabIndex={-1} />
        <Toaster />
      </>,
    );
    push('info', 'Saved');
    act(() => {
      screen.getByRole('button', { name: /Dismiss notification: Saved/ }).focus();
    });
    // Focus pauses the timer, so drop the toast the way the timer would.
    act(() => {
      useToastStore.getState().dismiss(useToastStore.getState().toasts[0]?.id ?? 0);
    });
    expect(document.getElementById('main')).toHaveFocus();
  });

  it('restores focus to the next toast when the cap drops the focused toast', () => {
    render(<Toaster />);
    push('info', 'I1');
    push('error', 'E1');
    act(() => {
      screen.getByRole('button', { name: 'Dismiss notification: I1' }).focus();
    });
    push('error', 'E2');
    push('error', 'E3'); // at the cap: drops I1, which held focus
    expect(screen.queryByText('I1')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Dismiss notification: E1' })).toHaveFocus();
  });

  it('does not add a second toast for an identical message of the same kind', () => {
    render(<Toaster />);
    const first = push('error', 'Same');
    const again = push('error', 'Same');
    expect(again).toBe(first);
    expect(count()).toBe(1);
    push('info', 'Same');
    expect(count()).toBe(2);
  });

  it('pauses the info timer while hovered and resumes with the time left', () => {
    vi.useFakeTimers();
    render(<Toaster />);
    push('info', 'Saved');
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    const toast = screen.getByText('Saved').closest('.toast');
    if (!toast) throw new Error('toast missing');
    act(() => {
      fireEvent.mouseEnter(toast);
      vi.advanceTimersByTime(60_000);
    });
    expect(count()).toBe(1);
    act(() => {
      fireEvent.mouseLeave(toast);
      vi.advanceTimersByTime(INFO_TOAST_MS - 3000 - 1);
    });
    expect(count()).toBe(1);
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(count()).toBe(0);
  });

  it('pauses the info timer while a button inside has focus', () => {
    vi.useFakeTimers();
    render(<Toaster />);
    push('info', 'Saved');
    const button = screen.getByRole('button', { name: /Dismiss notification: Saved/ });
    act(() => {
      button.focus();
      vi.advanceTimersByTime(60_000);
    });
    expect(count()).toBe(1);
    act(() => {
      button.blur();
      vi.advanceTimersByTime(INFO_TOAST_MS);
    });
    expect(count()).toBe(0);
  });

  it('moves focus to the next toast, then to #main, when the focused one is dismissed', async () => {
    render(
      <>
        <main id="main" tabIndex={-1} />
        <Toaster />
      </>,
    );
    push('error', 'One');
    push('error', 'Two');
    screen.getByRole('button', { name: 'Dismiss notification: One' }).focus();
    await userEvent.keyboard('{Enter}');
    expect(screen.getByRole('button', { name: 'Dismiss notification: Two' })).toHaveFocus();
    await userEvent.keyboard('{Enter}');
    expect(document.getElementById('main')).toHaveFocus();
  });

  it('does not steal focus when focus was outside the toaster', () => {
    render(
      <>
        <button type="button">elsewhere</button>
        <Toaster />
      </>,
    );
    push('error', 'One');
    const other = screen.getByRole('button', { name: 'elsewhere' });
    other.focus();
    // fireEvent.click does not move focus, so it stays outside the toaster.
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss notification: One' }));
    expect(other).toHaveFocus();
  });
});
