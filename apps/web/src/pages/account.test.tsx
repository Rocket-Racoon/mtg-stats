// S-2.1.8 decision 6: the /account page (change password) through the real route table and
// AppRoot, with a stubbed fetch. AC1 (success rotates the session), AC2 (400 on the current
// password), AC3 (422 on a bad new password).
import { StrictMode } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AppRoot } from '../providers/AppRoot';
import { routes } from '../routes';
import { useSessionStore } from '../session/store';

interface Call {
  path: string;
  method: string;
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
  details: unknown = null,
  headers: Record<string, string> = {},
) => json({ error: { code, message, details, request_id: 'req-1' } }, status, headers);

const OLD_ACCESS = 'access-before-change'; // secrets-check: allow
const NEW_ACCESS = 'access-after-change'; // secrets-check: allow
const CURRENT = 'current password 1'; // secrets-check: allow
const NEXT_PASSWORD = 'a brand new password'; // secrets-check: allow
const WRONG_MESSAGE = 'Current password is incorrect.';
const EMOJI = String.fromCodePoint(0x1f600);
const changed = () => json({ access_token: NEW_ACCESS, token_type: 'bearer', expires_in: 900 }); // secrets-check: allow

type Handler = (call: Call) => Response | Promise<Response>;

/** Stubs fetch. Every refresh answers 401 so a startup restore (if any) ends signed out. */
function stubApi(change: Handler = () => changed()) {
  const calls: Call[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = new Request(input, init);
      const text = await request.text();
      const call: Call = {
        path: new URL(request.url).pathname,
        method: request.method,
        headers: request.headers,
        body: text === '' ? null : (JSON.parse(text) as unknown),
      };
      calls.push(call);
      if (call.path === '/api/v1/auth/change-password') return change(call);
      if (call.path === '/api/v1/auth/refresh') {
        return failure(401, 'invalid_refresh', 'Session expired.');
      }
      return json({ status: 'ok' });
    }),
  );
  return {
    calls,
    changes: () => calls.filter((c) => c.path === '/api/v1/auth/change-password'),
  };
}

type Router = ReturnType<typeof createMemoryRouter>;

function mount(path = '/account'): Router {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(
    <StrictMode>
      <AppRoot router={router} />
    </StrictMode>,
  );
  return router;
}

const current = () => screen.getByLabelText('Current password');
const newPassword = () => screen.getByLabelText('New password');
const confirm = () => screen.getByLabelText('Confirm new password');
const submit = () => screen.getByRole('button', { name: /change password|changing password/i });
const ready = () => screen.findByRole('heading', { level: 2, name: 'Account' });

type User = ReturnType<typeof userEvent.setup>;

async function enter(user: User, input: HTMLElement, value: string) {
  await user.click(input);
  await user.paste(value);
}

async function fill(
  user: User,
  values: { current?: string; next?: string; confirm?: string } = {},
) {
  const next = values.next ?? NEXT_PASSWORD;
  await enter(user, current(), values.current ?? CURRENT);
  await enter(user, newPassword(), next);
  await enter(user, confirm(), values.confirm ?? next);
}

function describedText(input: HTMLElement): string {
  const ids = (input.getAttribute('aria-describedby') ?? '').split(/\s+/).filter(Boolean);
  return ids.map((id) => document.getElementById(id)?.textContent ?? '').join(' ');
}

beforeEach(() => {
  useSessionStore.setState({ status: 'signed_in', accessToken: OLD_ACCESS });
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  sessionStorage.clear();
  useSessionStore.setState({ status: 'signed_out', accessToken: null });
});

describe('decision 6: the /account route', () => {
  it('redirects a signed-out visitor to /sign-in?next=/account', async () => {
    useSessionStore.setState({ status: 'signed_out', accessToken: null });
    stubApi();
    const router = mount('/account');
    await waitFor(() => expect(router.state.location.pathname).toBe('/sign-in'));
    expect(router.state.location.search).toBe('?next=/account');
  });

  it('renders the Account page with a level-2 heading for a signed-in user', async () => {
    stubApi();
    const router = mount('/account');
    expect(await ready()).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/account');
    expect(document.title).toContain('Account');
  });

  it('lists Account in the navigation, linking to /account', () => {
    stubApi();
    mount('/');
    const links = screen.getAllByRole('link', { name: 'Account' });
    expect(links.length).toBeGreaterThanOrEqual(1);
    for (const link of links) expect(link).toHaveAttribute('href', '/account');
  });

  it('has a Change password section', async () => {
    stubApi();
    mount('/account');
    expect(await screen.findByRole('heading', { name: 'Change password' })).toBeInTheDocument();
  });
});

describe('the form is accessible and keyboard-operable', () => {
  it('has the three labelled password fields inside one real form', async () => {
    stubApi();
    mount('/account');
    await ready();
    for (const input of [current(), newPassword(), confirm()]) {
      expect(input).toHaveAttribute('type', 'password');
      expect(input.closest('form')).not.toBeNull();
    }
    expect(current().closest('form')).toBe(newPassword().closest('form'));
    expect(newPassword().closest('form')).toBe(confirm().closest('form'));
  });

  it('submits when Enter is pressed in the confirm field', async () => {
    const api = stubApi();
    const user = userEvent.setup();
    mount('/account');
    await ready();
    await fill(user);
    await user.keyboard('{Enter}');
    await waitFor(() => expect(api.changes()).toHaveLength(1));
  });
});

describe('client checks (no request is made)', () => {
  it('flags all three required fields next to themselves and focuses the first', async () => {
    const api = stubApi();
    const user = userEvent.setup();
    mount('/account');
    await ready();
    await user.click(submit());
    await waitFor(() => expect(current()).toHaveAttribute('aria-invalid', 'true'));
    for (const input of [current(), newPassword(), confirm()]) {
      expect(input).toHaveAttribute('aria-invalid', 'true');
      expect(describedText(input).trim()).not.toBe('');
      expect(input.closest('.field')!.querySelector('[role="alert"]')).not.toBeNull();
    }
    expect(current()).toHaveFocus();
    expect(api.changes()).toHaveLength(0);
  });

  it('focuses the new password when only it is missing', async () => {
    const api = stubApi();
    const user = userEvent.setup();
    mount('/account');
    await ready();
    await enter(user, current(), CURRENT);
    await user.click(submit());
    await waitFor(() => expect(newPassword()).toHaveAttribute('aria-invalid', 'true'));
    expect(current()).not.toHaveAttribute('aria-invalid', 'true');
    expect(newPassword()).toHaveFocus();
    expect(api.changes()).toHaveLength(0);
  });

  it('rejects a new password of 11 characters, flags it and focuses it', async () => {
    const api = stubApi();
    const user = userEvent.setup();
    mount('/account');
    await ready();
    await fill(user, { next: 'elevenchars' });
    await user.click(submit());
    await waitFor(() => expect(newPassword()).toHaveAttribute('aria-invalid', 'true'));
    expect(describedText(newPassword()).trim()).not.toBe('');
    expect(current()).not.toHaveAttribute('aria-invalid', 'true');
    expect(newPassword()).toHaveFocus();
    expect(api.changes()).toHaveLength(0);
  });

  it('counts code points, not UTF-16 units: 11 emoji are rejected', async () => {
    const api = stubApi();
    const user = userEvent.setup();
    mount('/account');
    await ready();
    await fill(user, { next: EMOJI.repeat(11) });
    await user.click(submit());
    await waitFor(() => expect(newPassword()).toHaveAttribute('aria-invalid', 'true'));
    expect(api.changes()).toHaveLength(0);
  });

  it('counts code points, not UTF-16 units: 12 emoji are accepted', async () => {
    const api = stubApi();
    const user = userEvent.setup();
    mount('/account');
    await ready();
    await fill(user, { next: EMOJI.repeat(12) });
    await user.click(submit());
    await waitFor(() => expect(api.changes()).toHaveLength(1));
    expect(api.changes()[0]!.body).toEqual({
      current_password: CURRENT,
      new_password: EMOJI.repeat(12),
    });
  });

  it('rejects a new password of 129 code points', async () => {
    const api = stubApi();
    const user = userEvent.setup();
    mount('/account');
    await ready();
    await fill(user, { next: 'a'.repeat(129) });
    await user.click(submit());
    await waitFor(() => expect(newPassword()).toHaveAttribute('aria-invalid', 'true'));
    expect(api.changes()).toHaveLength(0);
  });

  it('accepts a new password of exactly 128 code points', async () => {
    const api = stubApi();
    const user = userEvent.setup();
    mount('/account');
    await ready();
    await fill(user, { next: 'a'.repeat(128) });
    await user.click(submit());
    await waitFor(() => expect(api.changes()).toHaveLength(1));
  });

  it('shows "Passwords don\'t match." on the confirm field and focuses it', async () => {
    const api = stubApi();
    const user = userEvent.setup();
    mount('/account');
    await ready();
    await fill(user, { confirm: 'a different password' });
    await user.click(submit());
    expect(await screen.findByText("Passwords don't match.")).toBeInTheDocument();
    expect(describedText(confirm())).toContain("Passwords don't match.");
    expect(confirm()).toHaveAttribute('aria-invalid', 'true');
    expect(confirm().closest('.field')!.querySelector('[role="alert"]')).not.toBeNull();
    expect(newPassword()).not.toHaveAttribute('aria-invalid', 'true');
    expect(confirm()).toHaveFocus();
    expect(api.changes()).toHaveLength(0);
  });

  it('flags an empty confirm field as invalid and focuses it', async () => {
    const api = stubApi();
    const user = userEvent.setup();
    mount('/account');
    await ready();
    await enter(user, current(), CURRENT);
    await enter(user, newPassword(), NEXT_PASSWORD);
    await user.click(submit());
    await waitFor(() => expect(confirm()).toHaveAttribute('aria-invalid', 'true'));
    expect(confirm()).toHaveFocus();
    expect(api.changes()).toHaveLength(0);
  });
});

describe('AC1: success', () => {
  it('POSTs both passwords to /api/v1/auth/change-password with the old Bearer token', async () => {
    const api = stubApi();
    const user = userEvent.setup();
    mount('/account');
    await ready();
    await fill(user);
    await user.click(submit());
    await waitFor(() => expect(api.changes()).toHaveLength(1));
    const call = api.changes()[0]!;
    expect(call.method).toBe('POST');
    expect(call.headers.get('Authorization')).toBe(`Bearer ${OLD_ACCESS}`);
    expect(call.body).toEqual({ current_password: CURRENT, new_password: NEXT_PASSWORD });
  });

  it('stores the new access token, stays signed in, clears the fields and announces the success', async () => {
    stubApi();
    const user = userEvent.setup();
    const router = mount('/account');
    await ready();
    await fill(user);
    await user.click(submit());
    const message = await screen.findByText('Password changed.');
    expect(message.closest('[role="status"]')).not.toBeNull();
    expect(useSessionStore.getState().accessToken).toBe(NEW_ACCESS);
    expect(useSessionStore.getState().status).toBe('signed_in');
    expect(current()).toHaveValue('');
    expect(newPassword()).toHaveValue('');
    expect(confirm()).toHaveValue('');
    expect(router.state.location.pathname).toBe('/account');
    expect(screen.getAllByText('Password changed.')).toHaveLength(1);
  });

  it('sends the new Bearer token on the next API request', async () => {
    const api = stubApi();
    const user = userEvent.setup();
    mount('/account');
    await ready();
    await fill(user);
    await user.click(submit());
    await screen.findByText('Password changed.');
    const { api: client } = await import('../api');
    await client.GET('/api/v1/health/live');
    const live = api.calls.filter((c) => c.path === '/api/v1/health/live');
    expect(live).toHaveLength(1);
    expect(live[0]!.headers.get('Authorization')).toBe(`Bearer ${NEW_ACCESS}`);
  });

  it('never writes to Web Storage', async () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem');
    stubApi();
    const user = userEvent.setup();
    mount('/account');
    await ready();
    await fill(user);
    await user.click(submit());
    await screen.findByText('Password changed.');
    expect(setItem).not.toHaveBeenCalled();
    setItem.mockRestore();
  });

  it('disables submit and shows "Changing password…" while the request is pending', async () => {
    let release: (response: Response) => void = () => undefined;
    const api = stubApi(
      () =>
        new Promise<Response>((resolve) => {
          release = resolve;
        }),
    );
    const user = userEvent.setup();
    mount('/account');
    await ready();
    await fill(user);
    await user.click(submit());
    const pending = await screen.findByRole('button', { name: /changing password/i });
    expect(pending).toBeDisabled();
    expect(pending).toHaveTextContent('Changing password…');
    // Enter while pending sends nothing more.
    await user.keyboard('{Enter}');
    expect(api.changes()).toHaveLength(1);
    release(changed());
    await screen.findByText('Password changed.');
    expect(screen.getByRole('button', { name: 'Change password' })).toBeEnabled();
  });
});

describe('AC2: wrong current password (400)', () => {
  const wrong = () =>
    failure(400, 'invalid_current_password', WRONG_MESSAGE, {
      fields: [
        {
          loc: ['body', 'current_password'],
          path: 'body.current_password',
          message: WRONG_MESSAGE,
        },
      ],
    });

  it('shows the message on the current-password field, clears it and focuses it', async () => {
    stubApi(() => wrong());
    const user = userEvent.setup();
    const router = mount('/account');
    await ready();
    await fill(user);
    await user.click(submit());
    const message = await screen.findByText(WRONG_MESSAGE);
    expect(message.closest('[role="alert"]')).not.toBeNull();
    expect(current().closest('.field')).toContainElement(message);
    expect(current()).toHaveAttribute('aria-invalid', 'true');
    expect(describedText(current())).toContain(WRONG_MESSAGE);
    expect(current()).toHaveValue('');
    expect(current()).toHaveFocus();
    expect(router.state.location.pathname).toBe('/account');
    expect(useSessionStore.getState().accessToken).toBe(OLD_ACCESS);
    expect(screen.queryByText('Password changed.')).not.toBeInTheDocument();
  });

  it('shows the message once: no toast duplicates it', async () => {
    stubApi(() => wrong());
    const user = userEvent.setup();
    mount('/account');
    await ready();
    await fill(user);
    await user.click(submit());
    await screen.findByText(WRONG_MESSAGE);
    expect(screen.getAllByText(WRONG_MESSAGE)).toHaveLength(1);
  });

  it('can succeed on a retry after a 400', async () => {
    let attempt = 0;
    stubApi(() => (++attempt === 1 ? wrong() : changed()));
    const user = userEvent.setup();
    mount('/account');
    await ready();
    await fill(user);
    await user.click(submit());
    await screen.findByText(WRONG_MESSAGE);
    await fill(user);
    await user.click(submit());
    expect(await screen.findByText('Password changed.')).toBeInTheDocument();
    expect(screen.queryByText(WRONG_MESSAGE)).not.toBeInTheDocument();
    expect(useSessionStore.getState().accessToken).toBe(NEW_ACCESS);
  });
});

describe('AC3: server validation (422)', () => {
  const MESSAGE = 'Password is too common.';
  const invalid = () =>
    failure(422, 'validation_error', 'Invalid request.', {
      fields: [{ loc: ['body', 'new_password'], path: 'body.new_password', message: MESSAGE }],
    });

  it('maps the new_password field error to the new-password field', async () => {
    stubApi(() => invalid());
    const user = userEvent.setup();
    const router = mount('/account');
    await ready();
    await fill(user);
    await user.click(submit());
    const message = await screen.findByText(MESSAGE);
    expect(message.closest('[role="alert"]')).not.toBeNull();
    expect(newPassword().closest('.field')).toContainElement(message);
    expect(newPassword()).toHaveAttribute('aria-invalid', 'true');
    expect(describedText(newPassword())).toContain(MESSAGE);
    expect(current()).not.toHaveAttribute('aria-invalid', 'true');
    expect(router.state.location.pathname).toBe('/account');
    expect(useSessionStore.getState().accessToken).toBe(OLD_ACCESS);
    expect(screen.getAllByText(MESSAGE)).toHaveLength(1);
  });
});

describe('429 and other failures', () => {
  it.each([
    ['120', /^Too many attempts\. Try again in 2 minutes\.?$/],
    ['30', /^Too many attempts\. Try again in 1 minute\.?$/],
    ['61', /^Too many attempts\. Try again in 2 minutes\.?$/],
  ])('a 429 with Retry-After: %s shows the minutes text in an alert', async (retryAfter, text) => {
    stubApi(() => failure(429, 'rate_limited', 'Slow down.', null, { 'Retry-After': retryAfter }));
    const user = userEvent.setup();
    mount('/account');
    await ready();
    await fill(user);
    await user.click(submit());
    const message = await screen.findByText(text);
    expect(message.closest('[role="alert"]')).not.toBeNull();
    expect(screen.getAllByText(text)).toHaveLength(1);
    expect(useSessionStore.getState().accessToken).toBe(OLD_ACCESS);
    expect(screen.queryByText('Password changed.')).not.toBeInTheDocument();
  });

  it('a 500 shows the generic message once, in an alert', async () => {
    stubApi(() => failure(500, 'internal_error', 'Boom.'));
    const user = userEvent.setup();
    mount('/account');
    await ready();
    await fill(user);
    await user.click(submit());
    const message = await screen.findByText('Something went wrong. Try again.');
    expect(message.closest('[role="alert"]')).not.toBeNull();
    expect(screen.getAllByText('Something went wrong. Try again.')).toHaveLength(1);
    expect(screen.queryByText('Boom.')).not.toBeInTheDocument();
    expect(useSessionStore.getState().accessToken).toBe(OLD_ACCESS);
  });

  it('re-enables submit after a failure', async () => {
    stubApi(() => failure(500, 'internal_error', 'Boom.'));
    const user = userEvent.setup();
    mount('/account');
    await ready();
    await fill(user);
    await user.click(submit());
    await screen.findByText('Something went wrong. Try again.');
    expect(screen.getByRole('button', { name: 'Change password' })).toBeEnabled();
  });
});
