import { StrictMode, useEffect, type ReactNode } from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
  ApiError,
  createApiClient,
  defaultQueryClientConfig,
  useMutation,
  useQueryClient,
} from '@mtg-stats/api-client';
import { createMemoryRouter, type RouteObject } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ErrorBoundary } from '../errors/ErrorBoundary';
import { GENERIC_ERROR_MESSAGE, NETWORK_ERROR_MESSAGE } from '../errors/message';
import { routes } from '../routes';
import { useSessionStore } from '../session/store';
import { resetToasts, useToastStore } from '../ui/toasts';
import { AppProviders } from './AppProviders';
import { AppRoot } from './AppRoot';

// React and react-router log caught render errors; hide only those, let anything else through.
function silenceExpectedErrorLogs(): void {
  const original = console.error.bind(console);
  vi.spyOn(console, 'error').mockImplementation((...args: unknown[]) => {
    const text = args.map((a) => (a instanceof Error ? a.message : String(a))).join(' ');
    if (
      /boom-test|The above error occurred|Error: Uncaught|Error handled by React Router|Server fell over|Readable|req-|Nope/.test(
        text,
      )
    ) {
      return;
    }
    original(...args);
  });
}

function Boom({ error }: { error: unknown }): never {
  throw error;
}

function withBoomRoute(element: ReactNode): RouteObject[] {
  const root = routes[0];
  if (!root) throw new Error('routes is empty');
  const patched: RouteObject = {
    ...root,
    index: false,
    children: [...(root.children ?? []), { path: '/boom', element }],
  };
  return [patched, ...routes.slice(1)];
}

function renderRoot(table: RouteObject[], path: string) {
  const router = createMemoryRouter(table, { initialEntries: [path] });
  render(
    <StrictMode>
      <AppRoot router={router} />
    </StrictMode>,
  );
  return router;
}

beforeEach(() => {
  silenceExpectedErrorLogs();
  resetToasts();
  useSessionStore.setState({ status: 'signed_out' });
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('app root as main.tsx renders it (AC1)', () => {
  it('gives a deep route component a QueryClient with the S-1.5.4 defaults', () => {
    const expected = defaultQueryClientConfig().defaultOptions;
    let seen: ReturnType<ReturnType<typeof useQueryClient>['getDefaultOptions']> | undefined;
    function Probe() {
      const client = useQueryClient();
      useEffect(() => {
        seen = client.getDefaultOptions();
      }, [client]);
      return <p>probe</p>;
    }
    renderRoot(withBoomRoute(<Probe />), '/boom');
    expect(screen.getByText('probe')).toBeInTheDocument();
    expect(seen?.queries?.staleTime).toBe(expected?.queries?.staleTime);
    expect(seen?.queries?.retry).toBe(expected?.queries?.retry);
    expect(seen?.mutations?.retry).toBe(expected?.mutations?.retry);
  });

  it('keeps one QueryClient for the whole tree across navigation', async () => {
    const clients = new Set<unknown>();
    function Probe() {
      clients.add(useQueryClient());
      return <p>probe</p>;
    }
    const router = renderRoot(withBoomRoute(<Probe />), '/boom');
    await act(async () => {
      await router.navigate('/about');
    });
    await act(async () => {
      await router.navigate('/boom');
    });
    expect(clients.size).toBe(1);
  });

  it('lets a deep component read and update the session store', () => {
    function Probe() {
      const status = useSessionStore((s) => s.status);
      return <p>status {status}</p>;
    }
    renderRoot(withBoomRoute(<Probe />), '/boom');
    expect(screen.getByText('status signed_out')).toBeInTheDocument();
    act(() => {
      useSessionStore.getState().setStatus('restoring');
    });
    expect(screen.getByText('status restoring')).toBeInTheDocument();
    act(() => {
      useSessionStore.getState().setStatus('signed_in');
    });
    expect(screen.getByText('status signed_in')).toBeInTheDocument();
  });

  it('lets a deep component push UI state that the root toaster shows', () => {
    renderRoot(withBoomRoute(<p>probe</p>), '/boom');
    act(() => {
      useToastStore.getState().push('info', 'Saved deck');
    });
    expect(screen.getByRole('status')).toHaveTextContent('Saved deck');
  });
});

describe('render errors through the real route table (AC2)', () => {
  it('shows a fallback with a Reload button when a route throws, and Reload reloads the page', async () => {
    const reload = vi.fn();
    vi.stubGlobal('location', { ...window.location, reload });
    renderRoot(withBoomRoute(<Boom error={new Error('boom-test secret stack detail')} />), '/boom');
    expect(screen.getByRole('heading', { name: 'Something went wrong' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Reload' }));
    expect(reload).toHaveBeenCalledOnce();
  });

  it('shows the request id when the thrown error is an ApiError', () => {
    renderRoot(
      withBoomRoute(
        <Boom
          error={
            new ApiError({
              status: 500,
              code: 'internal_error',
              message: 'Server fell over',
              requestId: 'req-abc-123',
            })
          }
        />,
      ),
      '/boom',
    );
    expect(screen.getByText(/req-abc-123/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reload' })).toBeInTheDocument();
  });

  it('shows no request id for an ApiError without one', () => {
    renderRoot(
      withBoomRoute(
        <Boom error={new ApiError({ status: 500, code: 'internal_error', message: 'Nope' })} />,
      ),
      '/boom',
    );
    expect(screen.queryByText(/Request ID/i)).not.toBeInTheDocument();
  });

  it('shows no request id, stack or raw message for a plain Error', () => {
    const error = new Error('boom-test internal detail');
    error.stack = 'Error: boom-test internal detail\n    at secretFunction (file.ts:1:1)';
    renderRoot(withBoomRoute(<Boom error={error} />), '/boom');
    expect(screen.queryByText(/Request ID/i)).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/internal detail|secretFunction|file\.ts/);
    expect(screen.getByRole('button', { name: 'Reload' })).toBeInTheDocument();
  });

  it('does not render the thrown value as JSON or a stack trace', () => {
    renderRoot(
      withBoomRoute(
        <Boom
          error={new ApiError({ status: 500, code: 'x', message: 'Readable', requestId: 'r1' })}
        />,
      ),
      '/boom',
    );
    expect(document.body.textContent).not.toMatch(/[{}]|\bat \w+ \(/);
  });

  it('exposes the fallback as the single main landmark, named by its heading, with a Reload button', () => {
    renderRoot(withBoomRoute(<Boom error={new Error('boom-test')} />), '/boom');
    const main = screen.getByRole('main', { name: 'Something went wrong' });
    expect(main).toContainElement(screen.getByRole('button', { name: 'Reload' }));
    expect(document.querySelectorAll('main, #main')).toHaveLength(1);
  });

  it('shows the fallback only for the failing route', async () => {
    const router = renderRoot(withBoomRoute(<Boom error={new Error('boom-test')} />), '/about');
    expect(screen.queryByRole('button', { name: 'Reload' })).not.toBeInTheDocument();
    await act(async () => {
      await router.navigate('/boom');
    });
    expect(screen.getByRole('button', { name: 'Reload' })).toBeInTheDocument();
  });
});

describe('class ErrorBoundary outside the router (AC2)', () => {
  it('shows the fallback with Reload for a plain Error, with no request id', async () => {
    const reload = vi.fn();
    vi.stubGlobal('location', { ...window.location, reload });
    render(
      <ErrorBoundary>
        <Boom error={new Error('boom-test hidden')} />
      </ErrorBoundary>,
    );
    expect(screen.queryByText(/hidden/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Request ID/i)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Reload' }));
    expect(reload).toHaveBeenCalledOnce();
  });

  it('shows the request id for an ApiError', () => {
    render(
      <ErrorBoundary>
        <Boom error={new ApiError({ status: 500, code: 'c', message: 'm', requestId: 'req-77' })} />
      </ErrorBoundary>,
    );
    expect(screen.getByText(/req-77/)).toBeInTheDocument();
  });

  it('renders children untouched when nothing throws', () => {
    render(
      <ErrorBoundary>
        <p>fine</p>
      </ErrorBoundary>,
    );
    expect(screen.getByText('fine')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Reload' })).not.toBeInTheDocument();
  });
});

describe('failed mutations through the real api client (AC3)', () => {
  function jsonResponse(status: number, body: unknown): Response {
    return new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  function postWith(fetchImpl: () => Promise<Response>) {
    const client = createApiClient({
      baseUrl: 'http://api.test',
      fetch: fetchImpl as unknown as typeof globalThis.fetch,
    });
    return client.POST as unknown as (url: string, init?: object) => Promise<unknown>;
  }

  function Submit({
    post,
    meta,
  }: {
    post: () => Promise<unknown>;
    meta?: { errorToast: boolean };
  }) {
    const m = useMutation({ mutationFn: post, ...(meta ? { meta } : {}) });
    return (
      <>
        <button
          type="button"
          onClick={() => {
            m.mutate();
          }}
        >
          Submit
        </button>
        {meta?.errorToast === false && m.error ? (
          <p role="note">Inline: {m.error.message}</p>
        ) : null}
      </>
    );
  }

  async function submitAndGetAlert(post: () => Promise<unknown>) {
    render(
      <AppProviders>
        <Submit post={post} />
      </AppProviders>,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Submit' }));
    // The alert region is always present, so wait for the toast itself.
    await waitFor(() => {
      expect(useToastStore.getState().toasts).toHaveLength(1);
    });
    return screen.getByRole('alert');
  }

  const envelope = {
    error: {
      code: 'deck_name_taken',
      message: 'You already have a deck with that name.',
      details: { fields: [{ field: 'name', message: 'taken' }] },
      request_id: 'req-envelope-1',
    },
  };

  it('toasts exactly the envelope message for a 422 and no JSON', async () => {
    const post = postWith(() => Promise.resolve(jsonResponse(422, envelope)));
    const alert = await submitAndGetAlert(() => post('/api/v1/decks', { body: {} }));
    expect(alert).toHaveTextContent('You already have a deck with that name.');
    expect(alert.textContent).not.toContain('{');
    expect(alert.textContent).not.toContain('"code"');
    expect(alert.textContent).not.toContain('deck_name_taken');
    expect(alert.textContent).not.toContain('req-envelope-1');
    expect(useToastStore.getState().toasts).toHaveLength(1);
  });

  it.each([400, 401, 403, 404, 409, 500, 503])(
    'toasts the envelope message for HTTP %i',
    async (status) => {
      const post = postWith(() =>
        Promise.resolve(
          jsonResponse(status, { error: { ...envelope.error, message: `Message for ${status}` } }),
        ),
      );
      expect(await submitAndGetAlert(() => post('/api/v1/decks'))).toHaveTextContent(
        `Message for ${status}`,
      );
    },
  );

  it('never shows raw JSON when the envelope message is empty', async () => {
    const post = postWith(() =>
      Promise.resolve(jsonResponse(500, { error: { ...envelope.error, message: '' } })),
    );
    const alert = await submitAndGetAlert(() => post('/api/v1/decks'));
    expect(alert.textContent).toContain(GENERIC_ERROR_MESSAGE);
    expect(alert.textContent).not.toMatch(/[{}]|"code"/);
  });

  it('never shows raw JSON when the envelope has no message', async () => {
    const post = postWith(() =>
      Promise.resolve(jsonResponse(500, { error: { code: 'x', details: null, request_id: 'r' } })),
    );
    const alert = await submitAndGetAlert(() => post('/api/v1/decks'));
    expect(alert.textContent).not.toMatch(/[{}]|"code"|"error"/);
    expect(alert.textContent?.replace('Dismiss', '').trim()).not.toBe('');
  });

  it('shows a plain sentence when the error body is not JSON', async () => {
    const post = postWith(() =>
      Promise.resolve(
        new Response('<html><body>Bad gateway</body></html>', {
          status: 502,
          headers: { 'Content-Type': 'text/html' },
        }),
      ),
    );
    const alert = await submitAndGetAlert(() => post('/api/v1/decks'));
    expect(alert.textContent).not.toMatch(/[{}<>]/);
    expect(alert.textContent?.trim()).not.toBe('');
  });

  it('shows a plain sentence when the body is a JSON object that is not an envelope', async () => {
    const post = postWith(() => Promise.resolve(jsonResponse(500, { detail: 'raw', trace: [1] })));
    const alert = await submitAndGetAlert(() => post('/api/v1/decks'));
    expect(alert.textContent).not.toMatch(/[{}[\]]|"detail"/);
  });

  it('shows a plain sentence for a network failure', async () => {
    const post = postWith(() => Promise.reject(new TypeError('Failed to fetch')));
    const alert = await submitAndGetAlert(() => post('/api/v1/decks'));
    expect(alert).toHaveTextContent(NETWORK_ERROR_MESSAGE);
    expect(alert.textContent).not.toMatch(/[{}]|"code"|network_error|Failed to fetch/);
  });

  it('shows no toast with errorToast false, and the screen shows the message inline', async () => {
    const post = postWith(() => Promise.resolve(jsonResponse(422, envelope)));
    render(
      <AppProviders>
        <Submit post={() => post('/api/v1/decks')} meta={{ errorToast: false }} />
      </AppProviders>,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Submit' }));
    expect(await screen.findByRole('note')).toHaveTextContent(
      'Inline: You already have a deck with that name.',
    );
    expect(screen.getByRole('alert')).toBeEmptyDOMElement();
    expect(useToastStore.getState().toasts).toHaveLength(0);
  });

  it('does not retry a failed mutation: one failure is one request and one toast', async () => {
    const fetchSpy = vi.fn(() => Promise.resolve(jsonResponse(503, envelope)));
    const post = postWith(fetchSpy);
    await submitAndGetAlert(() => post('/api/v1/decks'));
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledTimes(1);
    });
    expect(useToastStore.getState().toasts).toHaveLength(1);
  });

  it('toasts the next failure too, and a toast is dismissed by its accessible button', async () => {
    const post = postWith(() => Promise.resolve(jsonResponse(422, envelope)));
    await submitAndGetAlert(() => post('/api/v1/decks'));
    await userEvent.click(
      screen.getByRole('button', {
        name: 'Dismiss notification: You already have a deck with that name.',
      }),
    );
    expect(screen.getByRole('alert')).toBeEmptyDOMElement();
    await userEvent.click(screen.getByRole('button', { name: 'Submit' }));
    expect(await screen.findByText('You already have a deck with that name.')).toBeInTheDocument();
    expect(useToastStore.getState().toasts).toHaveLength(1);
  });
});
