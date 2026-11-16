// S-2.1.6 AC3, AC4, AC7 (registration half): the invite page through the real route table and
// AppRoot, signed out, with a stubbed fetch.
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { QueryClient } from '@tanstack/react-query';
import { createMemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createAppQueryClient } from '../providers/AppProviders';
import { AppRoot } from '../providers/AppRoot';
import { routes } from '../routes';
import { useSessionStore } from '../session/store';

const TOKEN = 'inv-token-123'; // secrets-check: allow
const ACCESS = 'access-after-register'; // secrets-check: allow
const GONE_MESSAGE =
  'This invite has expired or was revoked. Ask your playgroup admin for a new one.';

const ANDROID = {
  userAgent:
    'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36',
  platform: 'Linux armv81',
  maxTouchPoints: 5,
};
const DESKTOP = {
  userAgent:
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
  platform: 'Win32',
};

interface Call {
  path: string;
  method: string;
  credentials: RequestCredentials;
  body: unknown;
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
const failure = (status: number, code: string, message: string, details: unknown = null) =>
  json({ error: { code, message, details, request_id: 'req-1' } }, status);
const preview = () =>
  json({ inviter_display_name: 'Ada Lovelace', expires_at: '2030-01-01T00:00:00Z' });
const gone = () => failure(410, 'invite_no_longer_valid', 'Invite no longer valid.');
const registered = () => json({ access_token: ACCESS, token_type: 'bearer', expires_in: 900 }, 201); // secrets-check: allow

type Handler = (call: Call) => Response | Promise<Response>;

function stubApi(handlers: { preview?: Handler; register?: Handler }) {
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
        body: text === '' ? null : (JSON.parse(text) as unknown),
      };
      calls.push(call);
      if (call.path === '/api/v1/invites/preview') return (handlers.preview ?? preview)(call);
      if (call.path === '/api/v1/auth/register') {
        return (handlers.register ?? registered)(call);
      }
      if (call.path === '/api/v1/auth/refresh') {
        return failure(401, 'invalid_refresh', 'Session expired.');
      }
      return json({ status: 'ok' });
    }),
  );
  return {
    calls,
    previews: () => calls.filter((c) => c.path === '/api/v1/invites/preview'),
    registers: () => calls.filter((c) => c.path === '/api/v1/auth/register'),
  };
}

type Router = ReturnType<typeof createMemoryRouter>;

function mount(path = `/invite/${TOKEN}`, queryClient?: QueryClient): Router {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(<AppRoot router={router} {...(queryClient ? { queryClient } : {})} />);
  return router;
}

const field = (label: RegExp) => screen.getByLabelText(label);
const usernameInput = () => field(/username/i);
const displayNameInput = () => field(/display name/i);
const passwordInput = () => field(/password/i);
const emailInput = () => field(/email/i);
const createButton = () =>
  screen.getByRole('button', { name: /register|create account|sign up|join/i });

async function typeInto(
  user: ReturnType<typeof userEvent.setup>,
  input: HTMLElement,
  value: string,
) {
  if (value.length > 30) {
    await user.click(input);
    await user.paste(value);
  } else {
    await user.type(input, value);
  }
}

async function fillForm(
  user: ReturnType<typeof userEvent.setup>,
  values: { username?: string; displayName?: string; password?: string; email?: string } = {},
) {
  await typeInto(user, usernameInput(), values.username ?? 'ada');
  await typeInto(user, displayNameInput(), values.displayName ?? 'Ada');
  await typeInto(user, passwordInput(), values.password ?? 'correct horse battery');
  if (values.email) await typeInto(user, emailInput(), values.email);
}

function describedText(input: HTMLElement): string {
  const ids = (input.getAttribute('aria-describedby') ?? '').split(/\s+/).filter(Boolean);
  return ids.map((id) => document.getElementById(id)?.textContent ?? '').join(' ');
}

beforeEach(() => {
  useSessionStore.setState({ status: 'signed_out', accessToken: null });
  vi.stubGlobal('navigator', DESKTOP);
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  sessionStorage.clear();
});

describe('AC3: a valid invite', () => {
  it('previews the token from the route and shows who invited the visitor', async () => {
    const api = stubApi({});
    mount();
    expect(await screen.findByText(/Ada Lovelace/)).toBeInTheDocument();
    expect(api.previews()).toHaveLength(1);
    expect(api.previews()[0]!.method).toBe('POST');
    expect(api.previews()[0]!.body).toEqual({ token: TOKEN });
  });

  it('shows a registration form with a label on every field', async () => {
    stubApi({});
    mount();
    await screen.findByText(/Ada Lovelace/);
    expect(usernameInput()).toBeInTheDocument();
    expect(displayNameInput()).toBeInTheDocument();
    expect(passwordInput()).toHaveAttribute('type', 'password');
    expect(emailInput()).toBeInTheDocument();
    for (const input of document.querySelectorAll('input')) {
      expect(input.labels?.length ?? 0).toBeGreaterThan(0);
    }
    expect(usernameInput().closest('form')).not.toBeNull();
  });

  it('marks only the email as optional', async () => {
    stubApi({});
    mount();
    await screen.findByText(/Ada Lovelace/);
    expect(screen.getByText(/optional/i)).toBeInTheDocument();
    expect(emailInput()).not.toBeRequired();
  });

  it('offers Open in app with an mtgstats://invite/<token> href on Android', async () => {
    stubApi({});
    vi.stubGlobal('navigator', ANDROID);
    mount();
    await screen.findByText(/Ada Lovelace/);
    expect(screen.getByRole('link', { name: 'Open in app' })).toHaveAttribute(
      'href',
      `mtgstats://invite/${TOKEN}`,
    );
  });

  it('offers no Open in app link on a desktop browser', async () => {
    stubApi({});
    mount();
    await screen.findByText(/Ada Lovelace/);
    expect(screen.queryByRole('link', { name: 'Open in app' })).not.toBeInTheDocument();
  });

  it('is reachable signed out without a redirect to sign-in', async () => {
    stubApi({});
    const router = mount();
    await screen.findByText(/Ada Lovelace/);
    expect(router.state.location.pathname).toBe(`/invite/${TOKEN}`);
  });
});

describe('AC3: an invalid invite', () => {
  it('shows the exact expired-or-revoked message and no form', async () => {
    stubApi({ preview: gone });
    mount();
    expect(await screen.findByText(GONE_MESSAGE)).toBeInTheDocument();
    expect(screen.queryByLabelText(/username/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/password/i)).not.toBeInTheDocument();
  });

  it('does not retry a 410', async () => {
    const api = stubApi({ preview: gone });
    mount();
    await screen.findByText(GONE_MESSAGE);
    expect(api.previews()).toHaveLength(1);
  });

  it('shows the generic message and a Retry button on a network error, and Retry recovers', async () => {
    let offline = true;
    const api = stubApi({
      preview: () => {
        if (offline) throw new TypeError('Failed to fetch');
        return preview();
      },
    });
    const user = userEvent.setup();
    mount();
    expect(await screen.findByText('Something went wrong. Try again.')).toBeInTheDocument();
    expect(screen.queryByLabelText(/username/i)).not.toBeInTheDocument();
    const before = api.previews().length;
    offline = false;
    await user.click(screen.getByRole('button', { name: /retry/i }));
    expect(await screen.findByText(/Ada Lovelace/)).toBeInTheDocument();
    expect(api.previews().length).toBeGreaterThan(before);
  });
});

describe('AC4: registering', () => {
  it('signs the person in and lands on / (replace), sending the documented body', async () => {
    const api = stubApi({});
    const user = userEvent.setup();
    const router = mount();
    await screen.findByText(/Ada Lovelace/);
    await fillForm(user, { email: 'ada@example.com' });
    await user.click(createButton());
    await waitFor(() => expect(router.state.location.pathname).toBe('/'));
    expect(router.state.historyAction).toBe('REPLACE');
    expect(useSessionStore.getState().status).toBe('signed_in');
    expect(useSessionStore.getState().accessToken).toBe(ACCESS);
    expect(api.registers()).toHaveLength(1);
    expect(api.registers()[0]!.method).toBe('POST');
    expect(api.registers()[0]!.credentials).toBe('include');
    expect(api.registers()[0]!.body).toEqual({
      invite_token: TOKEN, // secrets-check: allow
      username: 'ada',
      password: 'correct horse battery',
      display_name: 'Ada',
      email: 'ada@example.com',
    });
    expect(await screen.findByRole('heading', { level: 2, name: 'Home' })).toBeInTheDocument();
  });

  it('registers without an email', async () => {
    const api = stubApi({});
    const user = userEvent.setup();
    const router = mount();
    await screen.findByText(/Ada Lovelace/);
    await fillForm(user);
    await user.click(createButton());
    await waitFor(() => expect(router.state.location.pathname).toBe('/'));
    const body = api.registers()[0]!.body as Record<string, unknown>;
    expect(body.email === undefined || body.email === '').toBe(true);
  });

  it('trims the display name before sending it', async () => {
    const api = stubApi({});
    const user = userEvent.setup();
    const router = mount();
    await screen.findByText(/Ada Lovelace/);
    await fillForm(user, { displayName: '  Ada  ' });
    await user.click(createButton());
    await waitFor(() => expect(router.state.location.pathname).toBe('/'));
    expect((api.registers()[0]!.body as { display_name: string }).display_name).toBe('Ada');
  });

  it('leaves Web Storage empty after registering', async () => {
    stubApi({});
    const user = userEvent.setup();
    const router = mount();
    await screen.findByText(/Ada Lovelace/);
    await fillForm(user);
    await user.click(createButton());
    await waitFor(() => expect(router.state.location.pathname).toBe('/'));
    expect(localStorage).toHaveLength(0);
    expect(sessionStorage).toHaveLength(0);
  });

  it('shows a 409 username_taken message on the username field', async () => {
    stubApi({
      register: () => failure(409, 'username_taken', 'That username is already taken.'),
    });
    const user = userEvent.setup();
    const router = mount();
    await screen.findByText(/Ada Lovelace/);
    await fillForm(user);
    await user.click(createButton());
    await waitFor(() => expect(usernameInput()).toHaveAttribute('aria-invalid', 'true'));
    expect(describedText(usernameInput())).toContain('That username is already taken.');
    expect(usernameInput()).toHaveFocus();
    expect(router.state.location.pathname).toBe(`/invite/${TOKEN}`);
    expect(useSessionStore.getState().status).toBe('signed_out');
  });

  it('maps a 422 to the field named by the last element of each loc', async () => {
    stubApi({
      register: () =>
        failure(422, 'validation_error', 'Request validation failed.', {
          fields: [
            {
              loc: ['body', 'username'],
              path: 'body.username',
              message: 'Username is reserved.',
            },
            {
              loc: ['body', 'display_name'],
              path: 'body.display_name',
              message: 'Display name is not allowed.',
            },
          ],
        }),
    });
    const user = userEvent.setup();
    mount();
    await screen.findByText(/Ada Lovelace/);
    await fillForm(user);
    await user.click(createButton());
    await waitFor(() => expect(usernameInput()).toHaveAttribute('aria-invalid', 'true'));
    expect(describedText(usernameInput())).toContain('Username is reserved.');
    expect(displayNameInput()).toHaveAttribute('aria-invalid', 'true');
    expect(describedText(displayNameInput())).toContain('Display name is not allowed.');
    expect(passwordInput()).not.toHaveAttribute('aria-invalid', 'true');
  });

  it('replaces the form with the invalid-invite message on a 410 at submit', async () => {
    stubApi({ register: gone });
    const user = userEvent.setup();
    mount();
    await screen.findByText(/Ada Lovelace/);
    await fillForm(user);
    await user.click(createButton());
    expect(await screen.findByText(GONE_MESSAGE)).toBeInTheDocument();
    expect(screen.queryByLabelText(/username/i)).not.toBeInTheDocument();
    expect(useSessionStore.getState().status).toBe('signed_out');
  });

  it('shows the generic message on a server error and keeps the form', async () => {
    stubApi({ register: () => failure(500, 'internal_error', 'Boom.') });
    const user = userEvent.setup();
    mount();
    await screen.findByText(/Ada Lovelace/);
    await fillForm(user);
    await user.click(createButton());
    expect(await screen.findByText('Something went wrong. Try again.')).toBeInTheDocument();
    expect(usernameInput()).toHaveValue('ada');
  });

  it('does not toast the failure as well as showing it inline', async () => {
    stubApi({ register: () => failure(409, 'username_taken', 'That username is already taken.') });
    const user = userEvent.setup();
    mount();
    await screen.findByText(/Ada Lovelace/);
    await fillForm(user);
    await user.click(createButton());
    await waitFor(() => expect(usernameInput()).toHaveAttribute('aria-invalid', 'true'));
    expect(screen.getAllByText('That username is already taken.')).toHaveLength(1);
  });
});

describe('AC7: the registration form is keyboard-accessible', () => {
  it('submits when Enter is pressed in the last field', async () => {
    const api = stubApi({});
    const user = userEvent.setup();
    const router = mount();
    await screen.findByText(/Ada Lovelace/);
    await user.type(usernameInput(), 'ada');
    await user.type(displayNameInput(), 'Ada');
    await user.type(passwordInput(), 'correct horse battery{Enter}');
    await waitFor(() => expect(router.state.location.pathname).toBe('/'));
    expect(api.registers()).toHaveLength(1);
  });

  it('on an empty submit makes no request, flags the required fields and focuses the first', async () => {
    const api = stubApi({});
    const user = userEvent.setup();
    mount();
    await screen.findByText(/Ada Lovelace/);
    await user.click(createButton());
    await waitFor(() => expect(usernameInput()).toHaveAttribute('aria-invalid', 'true'));
    expect(displayNameInput()).toHaveAttribute('aria-invalid', 'true');
    expect(passwordInput()).toHaveAttribute('aria-invalid', 'true');
    expect(emailInput()).not.toHaveAttribute('aria-invalid', 'true');
    for (const input of [usernameInput(), displayNameInput(), passwordInput()]) {
      expect(describedText(input).trim()).not.toBe('');
    }
    expect(
      document
        .getElementById(usernameInput().getAttribute('aria-describedby')!.split(' ')[0]!)!
        .closest('[role="alert"]'),
    ).not.toBeNull();
    expect(usernameInput()).toHaveFocus();
    expect(api.registers()).toHaveLength(0);
  });

  it.each([
    ['a username shorter than 3 characters', { username: 'ab' }, 'username'],
    ['a username longer than 24 characters', { username: 'a'.repeat(25) }, 'username'],
    ['a username with a disallowed character', { username: 'ada lovelace' }, 'username'],
    ['a password shorter than 12 characters', { password: 'x'.repeat(11) }, 'password'],
    ['a password longer than 128 characters', { password: 'x'.repeat(129) }, 'password'],
    ['a display name of only spaces', { displayName: '   ' }, 'display'],
    ['a display name longer than 32 characters', { displayName: 'd'.repeat(33) }, 'display'],
    ['an email longer than 320 characters', { email: `${'e'.repeat(320)}@x.io` }, 'email'],
  ])('rejects %s before calling the API', async (_label, values, which) => {
    const api = stubApi({});
    const user = userEvent.setup();
    mount();
    await screen.findByText(/Ada Lovelace/);
    await fillForm(user, values);
    await user.click(createButton());
    const input = {
      username: usernameInput,
      password: passwordInput,
      display: displayNameInput,
      email: emailInput,
    }[which as 'username' | 'password' | 'display' | 'email']();
    await waitFor(() => expect(input).toHaveAttribute('aria-invalid', 'true'));
    expect(describedText(input).trim()).not.toBe('');
    expect(input).toHaveFocus();
    expect(api.registers()).toHaveLength(0);
  });

  it.each([
    ['a 3-character username', { username: 'abc' }],
    ['a 24-character username with . _ -', { username: 'a.b_c-d'.padEnd(24, 'x') }],
    ['a 12-character password', { password: 'x'.repeat(12) }],
    ['a 128-character password', { password: 'x'.repeat(128) }],
    ['a 32-character display name', { displayName: 'd'.repeat(32) }],
  ])('accepts %s at its limit', async (_label, values) => {
    const api = stubApi({});
    const user = userEvent.setup();
    mount();
    await screen.findByText(/Ada Lovelace/);
    await fillForm(user, values);
    await user.click(createButton());
    await waitFor(() => expect(api.registers()).toHaveLength(1));
  });

  it('announces the error in an alert element that the input points to', async () => {
    stubApi({});
    const user = userEvent.setup();
    mount();
    await screen.findByText(/Ada Lovelace/);
    await user.click(createButton());
    await waitFor(() => expect(usernameInput()).toHaveAttribute('aria-invalid', 'true'));
    const ids = (usernameInput().getAttribute('aria-describedby') ?? '').split(/\s+/);
    const targets = ids.map((id) => document.getElementById(id)).filter(Boolean) as HTMLElement[];
    expect(targets.length).toBeGreaterThan(0);
    expect(
      targets.some(
        (el) => el.closest('[role="alert"], [aria-live]') !== null && el.textContent !== '',
      ),
    ).toBe(true);
  });
});

describe('S-2.1.6 review: code-point length', () => {
  const EMOJI = '\u{1F600}';

  it.each([
    ['17 emoji', 17],
    ['32 emoji', 32],
  ])('accepts a display name of %s (counted in code points, not UTF-16 units)', async (_l, n) => {
    const api = stubApi({});
    const user = userEvent.setup();
    mount();
    await screen.findByText(/Ada Lovelace/);
    await fillForm(user, { displayName: EMOJI.repeat(n) });
    await user.click(createButton());
    await waitFor(() => expect(api.registers()).toHaveLength(1));
    expect((api.registers()[0]!.body as { display_name: string }).display_name).toBe(
      EMOJI.repeat(n),
    );
  });

  it('rejects a display name of 33 emoji before calling the API', async () => {
    const api = stubApi({});
    const user = userEvent.setup();
    mount();
    await screen.findByText(/Ada Lovelace/);
    await fillForm(user, { displayName: EMOJI.repeat(33) });
    await user.click(createButton());
    await waitFor(() => expect(displayNameInput()).toHaveAttribute('aria-invalid', 'true'));
    expect(displayNameInput()).toHaveFocus();
    expect(api.registers()).toHaveLength(0);
  });

  it('accepts a password of 65 emoji (65 code points, 130 UTF-16 units)', async () => {
    const api = stubApi({});
    const user = userEvent.setup();
    mount();
    await screen.findByText(/Ada Lovelace/);
    await fillForm(user, { password: EMOJI.repeat(65) });
    await user.click(createButton());
    await waitFor(() => expect(api.registers()).toHaveLength(1));
  });
});

describe('S-2.1.6 review: a 410 at submit', () => {
  it('moves focus to the heading or the message so the change is not silent', async () => {
    stubApi({ register: gone });
    const user = userEvent.setup();
    mount();
    await screen.findByText(/Ada Lovelace/);
    await fillForm(user);
    await user.click(createButton());
    const message = await screen.findByText(GONE_MESSAGE);
    await waitFor(() => {
      const active = document.activeElement as HTMLElement;
      expect(active).not.toBe(document.body);
      const heading = screen.getByRole('heading', { level: 2 });
      expect(active === heading || active.contains(message)).toBe(true);
    });
  });
});

describe('S-2.1.6 review: Retry pending state', () => {
  it('shows a pending state while the retry runs and sends one request for repeated clicks', async () => {
    let offline = true;
    let release: (response: Response) => void = () => undefined;
    const api = stubApi({
      preview: () => {
        if (offline) throw new TypeError('Failed to fetch');
        return new Promise<Response>((resolve) => {
          release = resolve;
        });
      },
    });
    const user = userEvent.setup();
    mount();
    await screen.findByText('Something went wrong. Try again.');
    const before = api.previews().length;
    offline = false;
    await user.click(screen.getByRole('button', { name: /retry/i }));
    await waitFor(() => expect(api.previews().length).toBe(before + 1));
    await waitFor(() => {
      const button = screen.queryByRole('button', { name: /retry|retrying/i });
      const pending =
        button === null ||
        (button as HTMLButtonElement).disabled ||
        button.getAttribute('aria-disabled') === 'true' ||
        button.getAttribute('aria-busy') === 'true' ||
        !/^retry$/i.test(button.textContent ?? '');
      expect(pending).toBe(true);
    });
    const again = screen.queryByRole('button', { name: /retry|retrying/i });
    if (again) await user.click(again);
    expect(api.previews().length).toBe(before + 1);
    release(preview());
    expect(await screen.findByText(/Ada Lovelace/)).toBeInTheDocument();
  });
});

describe('S-2.1.6 review: the preview query key', () => {
  it('is cached under the playgroups domain', async () => {
    stubApi({});
    const qc = createAppQueryClient();
    mount(undefined, qc);
    await screen.findByText(/Ada Lovelace/);
    const keys = qc
      .getQueryCache()
      .getAll()
      .map((q) => q.queryKey);
    expect(keys).toHaveLength(1);
    expect(keys[0]![0]).toBe('playgroups');
    expect(JSON.stringify(keys[0])).toContain(TOKEN);
  });
});
