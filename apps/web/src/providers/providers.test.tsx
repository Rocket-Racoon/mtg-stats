import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ApiError, useMutation, useQueryClient } from '@mtg-stats/api-client';
import { createMemoryRouter } from 'react-router';
import { RouterProvider } from 'react-router/dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { GENERIC_ERROR_MESSAGE, errorMessage } from '../errors/message';
import { useSessionStore } from '../session/store';
import { Toaster } from '../ui/Toaster';
import { useToastStore } from '../ui/toasts';
import { AppProviders, createAppQueryClient } from './AppProviders';

function apiError(init: Partial<ConstructorParameters<typeof ApiError>[0]> = {}) {
  return new ApiError({ status: 422, code: 'validation_error', message: 'Name is taken', ...init });
}

function Boom({ error }: { error: unknown }): never {
  throw error;
}

beforeEach(() => {
  vi.spyOn(console, 'error').mockImplementation(() => undefined);
  useToastStore.setState({ toasts: [] });
});
afterEach(() => {
  vi.restoreAllMocks();
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
    expect(screen.getByRole('heading', { name: 'Something went wrong' })).toBeInTheDocument();
    expect(screen.queryByText(/secret stack detail/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Request ID/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Reload' }));
    expect(reload).toHaveBeenCalledOnce();
    vi.unstubAllGlobals();
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
  function Mutate({ error, meta }: { error: unknown; meta?: { errorToast: boolean } }) {
    const m = useMutation({
      mutationFn: () => Promise.reject(error as Error),
      ...(meta ? { meta } : {}),
    });
    return (
      <button
        type="button"
        onClick={() => {
          m.mutate();
        }}
      >
        Go
      </button>
    );
  }

  it('toasts the envelope message, not JSON, and the toast is dismissible', async () => {
    render(
      <AppProviders>
        <Mutate error={apiError()} />
      </AppProviders>,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Go' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Name is taken');
    expect(alert.textContent).not.toMatch(/[{}]/);
    await userEvent.click(screen.getByRole('button', { name: /Dismiss notification/ }));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('shows a generic sentence for a non-ApiError', async () => {
    render(
      <AppProviders>
        <Mutate error={new TypeError('x is undefined')} />
      </AppProviders>,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Go' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(GENERIC_ERROR_MESSAGE);
  });

  it('does not toast when the mutation opts out', async () => {
    render(
      <AppProviders>
        <Mutate error={apiError()} meta={{ errorToast: false }} />
      </AppProviders>,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Go' }));
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('createAppQueryClient keeps the defaults', () => {
    expect(createAppQueryClient().getDefaultOptions().queries?.staleTime).toBe(30_000);
  });
});

describe('errorMessage', () => {
  it('returns the envelope message for ApiError, including network_error', () => {
    expect(errorMessage(apiError())).toBe('Name is taken');
    expect(errorMessage(apiError({ status: 0, code: 'network_error', message: 'Offline' }))).toBe(
      'Offline',
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
  it('uses role=status for info toasts and auto-dismisses them', () => {
    vi.useFakeTimers();
    render(<Toaster />);
    act(() => {
      useToastStore.getState().push('info', 'Saved');
    });
    expect(screen.getByRole('status')).toHaveTextContent('Saved');
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('keeps error toasts until dismissed', () => {
    vi.useFakeTimers();
    render(<Toaster />);
    act(() => {
      useToastStore.getState().push('error', 'Nope');
      vi.advanceTimersByTime(60_000);
    });
    expect(screen.getByRole('alert')).toHaveTextContent('Nope');
  });
});
