// S-2.1.6 AC1, AC2, AC7 (sign-in half) and AC5 (storage): the sign-in page through the real route
// table and AppRoot, with a stubbed fetch.
import { StrictMode } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AppRoot } from '../providers/AppRoot';
import { routes } from '../routes';
import { useSessionStore } from './store';

interface Call {
  path: string;
  method: string;
  credentials: RequestCredentials;
  headers: Headers;
  body: unknown;
}

const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  });
const failure = (
  status: number,
  code: string,
  message: string,
  headers: Record<string, string> = {},
) => json({ error: { code, message, details: null, request_id: 'req-1' } }, status, headers);

const ACCESS = 'access-token-from-login'; // secrets-check: allow
const tokenResponse = () => json({ access_token: ACCESS, token_type: 'bearer', expires_in: 900 }); // secrets-check: allow

type Handler = (call: Call) => Response | Promise<Response>;

/** Stubs fetch. Every refresh answers 401 so a startup restore (if any) ends signed out. */
function stubApi(login: Handler) {
  const calls: Call[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = new Request(input, init);
      const text = await request.text();
      const call: Call = {
        path: new URL(request.url).pathname,
        method: request.method,
        credentials: request.credentials,
        headers: request.headers,
        body: text === '' ? null : (JSON.parse(text) as unknown),
      };
      calls.push(call);
      if (call.path === '/api/v1/auth/login') return login(call);
      if (call.path === '/api/v1/auth/refresh') {
        return failure(401, 'invalid_refresh', 'Session expired.');
      }
      return json({ status: 'ok' });
    }),
  );
  return { calls, logins: () => calls.filter((c) => c.path === '/api/v1/auth/login') };
}

type Router = ReturnType<typeof createMemoryRouter>;

function mount(path: string): Router {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(
    <StrictMode>
      <AppRoot router={router} />
    </StrictMode>,
  );
  return router;
}

const username = () => screen.getByLabelText(/username/i);
const password = () => screen.getByLabelText(/password/i);
const submit = () => screen.getByRole('button', { name: /sign in/i });

async function fill(user: ReturnType<typeof userEvent.setup>, name: string, pass: string) {
  await user.type(username(), name);
  await user.type(password(), pass);
}

function describedText(input: HTMLElement): string {
  const ids = (input.getAttribute('aria-describedby') ?? '').split(/\s+/).filter(Boolean);
  return ids.map((id) => document.getElementById(id)?.textContent ?? '').join(' ');
}

beforeEach(() => {
  useSessionStore.setState({ status: 'signed_out', accessToken: null });
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  sessionStorage.clear();
});

describe('AC1: where a successful sign-in lands', () => {
  it('signs in with username and password and lands on /decks when next=/decks', async () => {
    const api = stubApi(() => tokenResponse());
    const user = userEvent.setup();
    const router = mount('/sign-in?next=/decks');
    await fill(user, 'ada', 'correct horse battery');
    await user.click(submit());
    await waitFor(() => expect(router.state.location.pathname).toBe('/decks'));
    expect(await screen.findByRole('heading', { level: 2, name: 'Decks' })).toBeInTheDocument();
    expect(api.logins()).toHaveLength(1);
    expect(api.logins()[0]!.method).toBe('POST');
    expect(api.logins()[0]!.credentials).toBe('include');
    expect(api.logins()[0]!.body).toEqual({ username: 'ada', password: 'correct horse battery' });
  });

  it('lands on the playgroup home (/) when there is no next', async () => {
    stubApi(() => tokenResponse());
    const user = userEvent.setup();
    const router = mount('/sign-in');
    await fill(user, 'ada', 'correct horse battery');
    await user.click(submit());
    await waitFor(() => expect(router.state.location.pathname).toBe('/'));
    expect(router.state.location.search).toBe('');
    expect(await screen.findByRole('heading', { level: 2, name: 'Home' })).toBeInTheDocument();
  });

  it('replaces the sign-in entry so Back does not return to the form', async () => {
    stubApi(() => tokenResponse());
    const user = userEvent.setup();
    const router = mount('/sign-in?next=/decks');
    await fill(user, 'ada', 'correct horse battery');
    await user.click(submit());
    await waitFor(() => expect(router.state.location.pathname).toBe('/decks'));
    expect(router.state.historyAction).toBe('REPLACE');
  });

  it('keeps the query string and hash of a same-origin next', async () => {
    stubApi(() => tokenResponse());
    const user = userEvent.setup();
    const next = encodeURIComponent('/stats?range=30d#top');
    const router = mount(`/sign-in?next=${next}`);
    await fill(user, 'ada', 'correct horse battery');
    await user.click(submit());
    await waitFor(() => expect(router.state.location.pathname).toBe('/stats'));
    expect(router.state.location.search).toBe('?range=30d');
    expect(router.state.location.hash).toBe('#top');
  });

  it.each([
    ['a full https URL', 'https://evil.example/decks'],
    ['a protocol-relative URL', '//evil.example'],
    ['a slash-backslash URL', '/\\evil.example'],
    ['a javascript: URL', 'javascript:alert(1)'],
    ['an encoded protocol-relative URL', '%2F%2Fevil.example'],
    ['an encoded backslash URL', '%2F%5Cevil.example'],
    ['a tab inside the slashes', '/\t/evil.example'],
    ['a dot-segment collapsing to //host', '/..//evil.example'],
    ['no leading slash', 'decks'],
  ])('ignores a next that is %s and lands on /', async (_label, raw) => {
    stubApi(() => tokenResponse());
    const user = userEvent.setup();
    const router = mount(`/sign-in?next=${encodeURIComponent(raw)}`);
    await fill(user, 'ada', 'correct horse battery');
    await user.click(submit());
    await waitFor(() => expect(router.state.location.pathname).toBe('/'));
    expect(router.state.location.search).toBe('');
    expect(window.location.hostname).not.toBe('evil.example');
  });

  it('redirects a signed-in visitor on /sign-in to a same-origin next', () => {
    useSessionStore.setState({ status: 'signed_in', accessToken: ACCESS });
    stubApi(() => tokenResponse());
    const router = mount('/sign-in?next=/decks');
    expect(router.state.location.pathname).toBe('/decks');
    expect(router.state.historyAction).toBe('REPLACE');
  });

  it('redirects a signed-in visitor on /sign-in to / when there is no next', () => {
    useSessionStore.setState({ status: 'signed_in', accessToken: ACCESS });
    stubApi(() => tokenResponse());
    const router = mount('/sign-in');
    expect(router.state.location.pathname).toBe('/');
  });

  it('redirects a signed-in visitor to / when next is not same-origin', () => {
    useSessionStore.setState({ status: 'signed_in', accessToken: ACCESS });
    stubApi(() => tokenResponse());
    const router = mount(`/sign-in?next=${encodeURIComponent('https://evil.example')}`);
    expect(router.state.location.pathname).toBe('/');
  });
});

describe('AC2: wrong credentials and rate limiting', () => {
  it('shows the envelope message, clears the password and focuses it', async () => {
    const api = stubApi(() =>
      failure(401, 'invalid_credentials', 'Incorrect username or password.'),
    );
    const user = userEvent.setup();
    const router = mount('/sign-in?next=/decks');
    await fill(user, 'ada', 'wrong password!!');
    await user.click(submit());
    expect(await screen.findByText('Incorrect username or password.')).toBeInTheDocument();
    expect(password()).toHaveValue('');
    expect(password()).toHaveFocus();
    expect(username()).toHaveValue('ada');
    expect(router.state.location.pathname).toBe('/sign-in');
    expect(useSessionStore.getState().status).toBe('signed_out');
    expect(api.logins()).toHaveLength(1);
  });

  it('announces the error in an alert and does not also toast it', async () => {
    stubApi(() => failure(401, 'invalid_credentials', 'Incorrect username or password.'));
    const user = userEvent.setup();
    mount('/sign-in');
    await fill(user, 'ada', 'wrong password!!');
    await user.click(submit());
    const message = await screen.findByText('Incorrect username or password.');
    expect(message.closest('[role="alert"]')).not.toBeNull();
    expect(screen.getAllByText('Incorrect username or password.')).toHaveLength(1);
  });

  it('can sign in after a failed attempt', async () => {
    let attempt = 0;
    stubApi(() =>
      ++attempt === 1
        ? failure(401, 'invalid_credentials', 'Incorrect username or password.')
        : tokenResponse(),
    );
    const user = userEvent.setup();
    const router = mount('/sign-in?next=/decks');
    await fill(user, 'ada', 'wrong password!!');
    await user.click(submit());
    await screen.findByText('Incorrect username or password.');
    await user.type(password(), 'correct horse battery');
    await user.click(submit());
    await waitFor(() => expect(router.state.location.pathname).toBe('/decks'));
  });

  it.each([
    ['120', /^Too many attempts\. Try again in 2 minutes\.?$/],
    ['30', /^Too many attempts\. Try again in 1 minute\.?$/],
    ['60', /^Too many attempts\. Try again in 1 minute\.?$/],
    ['61', /^Too many attempts\. Try again in 2 minutes\.?$/],
    ['3600', /^Too many attempts\. Try again in 60 minutes\.?$/],
  ])('a 429 with Retry-After: %s shows the minutes text', async (retryAfter, expected) => {
    stubApi(() => failure(429, 'rate_limited', 'Slow down.', { 'Retry-After': retryAfter }));
    const user = userEvent.setup();
    mount('/sign-in');
    await fill(user, 'ada', 'correct horse battery');
    await user.click(submit());
    expect(await screen.findByText(expected)).toBeInTheDocument();
    expect(screen.getByText(expected).closest('[role="alert"]')).not.toBeNull();
  });

  it('a 429 with an HTTP-date Retry-After rounds the remaining time up to minutes', async () => {
    // 10 minutes and 30 seconds ahead: ceil gives 11 whatever the test's own latency.
    const date = new Date(Date.now() + 630_000).toUTCString();
    stubApi(() => failure(429, 'rate_limited', 'Slow down.', { 'Retry-After': date }));
    const user = userEvent.setup();
    mount('/sign-in');
    await fill(user, 'ada', 'correct horse battery');
    await user.click(submit());
    expect(
      await screen.findByText(/^Too many attempts\. Try again in 11 minutes\.?$/),
    ).toBeInTheDocument();
  });

  it('a 429 without Retry-After shows the fallback, with no minute count', async () => {
    stubApi(() => failure(429, 'rate_limited', 'Slow down.'));
    const user = userEvent.setup();
    mount('/sign-in');
    await fill(user, 'ada', 'correct horse battery');
    await user.click(submit());
    const text = await screen.findByText(/Too many attempts/);
    expect(text.closest('[role="alert"]')).not.toBeNull();
    expect(text).not.toHaveTextContent(/\d+ minutes?/);
  });

  it('a 500 shows the generic message', async () => {
    stubApi(() => failure(500, 'internal_error', 'Boom.'));
    const user = userEvent.setup();
    mount('/sign-in');
    await fill(user, 'ada', 'correct horse battery');
    await user.click(submit());
    expect(await screen.findByText('Something went wrong. Try again.')).toBeInTheDocument();
  });

  it('a network failure shows the generic message', async () => {
    stubApi(() => {
      throw new TypeError('Failed to fetch');
    });
    const user = userEvent.setup();
    mount('/sign-in');
    await fill(user, 'ada', 'correct horse battery');
    await user.click(submit());
    expect(await screen.findByText('Something went wrong. Try again.')).toBeInTheDocument();
  });
});

describe('AC7: the sign-in form is keyboard-accessible', () => {
  it('has a label on every input and a real form', () => {
    stubApi(() => tokenResponse());
    mount('/sign-in');
    const inputs = document.querySelectorAll('input');
    expect(inputs.length).toBeGreaterThanOrEqual(2);
    for (const input of inputs) {
      expect(input.labels?.length ?? 0).toBeGreaterThan(0);
    }
    expect(username().closest('form')).not.toBeNull();
    expect(password()).toHaveAttribute('type', 'password');
  });

  it('submits when Enter is pressed in the password field', async () => {
    const api = stubApi(() => tokenResponse());
    const user = userEvent.setup();
    const router = mount('/sign-in?next=/decks');
    await user.type(username(), 'ada');
    await user.type(password(), 'correct horse battery{Enter}');
    await waitFor(() => expect(router.state.location.pathname).toBe('/decks'));
    expect(api.logins()).toHaveLength(1);
  });

  it('on an empty submit makes no request and flags both fields next to themselves', async () => {
    const api = stubApi(() => tokenResponse());
    const user = userEvent.setup();
    mount('/sign-in');
    await user.click(submit());
    await waitFor(() => expect(username()).toHaveAttribute('aria-invalid', 'true'));
    expect(password()).toHaveAttribute('aria-invalid', 'true');
    expect(describedText(username()).trim()).not.toBe('');
    expect(describedText(password()).trim()).not.toBe('');
    expect(username().closest('.field')!.querySelector('[role="alert"]')).not.toBeNull();
    expect(username()).toHaveFocus();
    expect(api.logins()).toHaveLength(0);
  });

  it('moves focus to the password when only it is missing', async () => {
    const api = stubApi(() => tokenResponse());
    const user = userEvent.setup();
    mount('/sign-in');
    await user.type(username(), 'ada');
    await user.click(submit());
    await waitFor(() => expect(password()).toHaveAttribute('aria-invalid', 'true'));
    expect(username()).not.toHaveAttribute('aria-invalid', 'true');
    expect(password()).toHaveFocus();
    expect(api.logins()).toHaveLength(0);
  });
});

describe('AC5: the token is not persisted', () => {
  it('leaves localStorage and sessionStorage empty after a sign-in', async () => {
    stubApi(() => tokenResponse());
    const user = userEvent.setup();
    const router = mount('/sign-in?next=/decks');
    await fill(user, 'ada', 'correct horse battery');
    await user.click(submit());
    await waitFor(() => expect(router.state.location.pathname).toBe('/decks'));
    expect(localStorage).toHaveLength(0);
    expect(sessionStorage).toHaveLength(0);
    for (const store of [localStorage, sessionStorage]) {
      for (let i = 0; i < store.length; i += 1) {
        const key = store.key(i)!;
        expect(key).not.toContain(ACCESS);
        expect(store.getItem(key)).not.toContain(ACCESS);
      }
    }
  });

  it('holds the access token in the session store and marks the session signed in', async () => {
    stubApi(() => tokenResponse());
    const user = userEvent.setup();
    const router = mount('/sign-in');
    await fill(user, 'ada', 'correct horse battery');
    await user.click(submit());
    await waitFor(() => expect(router.state.location.pathname).toBe('/'));
    expect(useSessionStore.getState().status).toBe('signed_in');
    expect(useSessionStore.getState().accessToken).toBe(ACCESS);
  });

  it('never writes to Web Storage during sign-in', async () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem');
    stubApi(() => tokenResponse());
    const user = userEvent.setup();
    const router = mount('/sign-in');
    await fill(user, 'ada', 'correct horse battery');
    await user.click(submit());
    await waitFor(() => expect(router.state.location.pathname).toBe('/'));
    expect(setItem).not.toHaveBeenCalled();
    setItem.mockRestore();
  });
});

describe('AC6: requests after sign-in carry the Bearer token', () => {
  it('sends the access token from the sign-in on the next API request', async () => {
    const api = stubApi(() => tokenResponse());
    const user = userEvent.setup();
    const router = mount('/sign-in');
    await fill(user, 'ada', 'correct horse battery');
    await user.click(submit());
    await waitFor(() => expect(router.state.location.pathname).toBe('/'));
    const { api: client } = await import('../api');
    await client.GET('/api/v1/health/live');
    const live = api.calls.filter((c) => c.path === '/api/v1/health/live');
    expect(live).toHaveLength(1);
    expect(live[0]!.headers.get('Authorization')).toBe(`Bearer ${ACCESS}`);
  });
});
