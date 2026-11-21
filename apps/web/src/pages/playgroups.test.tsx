// S-2.4.1 T-2.4.1.4: the /playgroups page (create form and list) through the real route table and
// AppRoot, with a stubbed fetch.
import { StrictMode } from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AppRoot } from '../providers/AppRoot';
import { routes } from '../routes';
import { useSessionStore } from '../session/store';
import { resetToasts } from '../ui/toasts';
import { validatePlaygroup } from './PlaygroupsPage';

interface Call {
  path: string;
  method: string;
  body: unknown;
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
const failure = (status: number, code: string, message: string, details: unknown = null) =>
  json({ error: { code, message, details, request_id: 'req-1' } }, status);

const group = (name: string, role = 'owner') => ({
  id: `id-${name}`,
  name,
  description: '',
  role,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  joined_at: '2026-01-01T00:00:00Z',
});

/** Stubs fetch with a mutable server-side list; POST appends and returns the new group. */
function stubApi(post?: (call: Call) => Response | Promise<Response>) {
  const calls: Call[] = [];
  const items = [group('Existing crew', 'member')];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = new Request(input, init);
      const text = await request.text();
      const call: Call = {
        path: new URL(request.url).pathname,
        method: request.method,
        body: text === '' ? null : (JSON.parse(text) as unknown),
      };
      calls.push(call);
      if (call.path === '/api/v1/playgroups' && call.method === 'GET') {
        return json({ items, next_cursor: null });
      }
      if (call.path === '/api/v1/playgroups' && call.method === 'POST') {
        if (post) return post(call);
        const body = call.body as { name: string };
        const created = group(body.name);
        items.unshift(created);
        return json(created, 201);
      }
      return failure(401, 'invalid_refresh', 'Session expired.');
    }),
  );
  return {
    posts: () => calls.filter((c) => c.method === 'POST' && c.path.endsWith('/playgroups')),
  };
}

function mount() {
  const router = createMemoryRouter(routes, { initialEntries: ['/playgroups'] });
  render(
    <StrictMode>
      <AppRoot router={router} />
    </StrictMode>,
  );
}

const nameField = () => screen.getByLabelText('Name');
const descriptionField = () => screen.getByLabelText('Description (optional)');
const submit = () => screen.getByRole('button', { name: /create playgroup|creating/i });

beforeEach(() => {
  useSessionStore.setState({ status: 'signed_in', accessToken: 'access' }); // secrets-check: allow
});

afterEach(() => {
  vi.unstubAllGlobals();
  resetToasts();
  localStorage.clear();
  sessionStorage.clear();
  useSessionStore.setState({ status: 'signed_out', accessToken: null });
});

describe('validatePlaygroup', () => {
  it('trims the name before counting and accepts the bounds', () => {
    expect(validatePlaygroup('  ab  ', '')).toHaveProperty('name');
    expect(validatePlaygroup('  abc  ', '')).toEqual({});
    expect(validatePlaygroup('a'.repeat(50), 'd'.repeat(500))).toEqual({});
    expect(validatePlaygroup('a'.repeat(51), '')).toHaveProperty('name');
    expect(validatePlaygroup('abc', 'd'.repeat(501))).toHaveProperty('description');
  });
});

describe('PlaygroupsPage', () => {
  it('lists the caller playgroups with their role', async () => {
    stubApi();
    mount();
    expect(await screen.findByText('Existing crew (member)')).toBeInTheDocument();
  });

  it('shows a name error without calling the API, and focuses the name', async () => {
    const api = stubApi();
    const user = userEvent.setup();
    mount();
    await screen.findByText('Existing crew (member)');
    await user.type(nameField(), 'ab');
    await user.click(submit());
    expect(nameField()).toHaveAttribute('aria-invalid', 'true');
    expect(nameField()).toHaveAccessibleDescription('Name must be 3 to 50 characters.');
    expect(nameField()).toHaveFocus();
    expect(api.posts()).toHaveLength(0);
  });

  it('creates with a trimmed name, submits from the keyboard, refreshes the list', async () => {
    const api = stubApi();
    const user = userEvent.setup();
    mount();
    await screen.findByText('Existing crew (member)');
    await user.type(nameField(), '  Friday Night  ');
    await user.type(descriptionField(), 'Commander');
    await user.type(nameField(), '{Enter}');
    expect(await screen.findByText('Friday Night (owner)')).toBeInTheDocument();
    expect(api.posts()[0]?.body).toEqual({ name: 'Friday Night', description: 'Commander' });
    expect(await screen.findByText('Created playgroup Friday Night.')).toBeInTheDocument();
    expect(nameField()).toHaveValue('');
  });

  it('maps a server validation_error onto the field', async () => {
    stubApi(() =>
      failure(422, 'validation_error', 'Invalid.', {
        fields: [{ loc: ['body', 'name'], message: 'Name is already weird.' }],
      }),
    );
    const user = userEvent.setup();
    mount();
    await screen.findByText('Existing crew (member)');
    await user.type(nameField(), 'Valid name');
    await user.click(submit());
    await waitFor(() => {
      expect(nameField()).toHaveAccessibleDescription('Name is already weird.');
    });
    expect(nameField()).toHaveValue('Valid name');
  });

  it('shows other errors as a toast and keeps the input', async () => {
    stubApi(() => failure(500, 'internal_error', 'The server broke.'));
    const user = userEvent.setup();
    mount();
    await screen.findByText('Existing crew (member)');
    await user.type(nameField(), 'Valid name');
    await user.click(submit());
    expect(await screen.findByText('The server broke.')).toBeInTheDocument();
    expect(nameField()).toHaveValue('Valid name');
  });

  it('disables the button while the request is pending so it cannot double submit', async () => {
    let release: (() => void) | undefined;
    const api = stubApi(
      (call) =>
        new Promise<Response>((resolve) => {
          release = () => {
            resolve(json(group((call.body as { name: string }).name), 201));
          };
        }),
    );
    const user = userEvent.setup();
    mount();
    await screen.findByText('Existing crew (member)');
    await user.type(nameField(), 'Valid name');
    await user.click(submit());
    await waitFor(() => {
      expect(submit()).toBeDisabled();
    });
    await user.type(nameField(), '{Enter}');
    expect(api.posts()).toHaveLength(1);
    release?.();
    await waitFor(() => {
      expect(submit()).toBeEnabled();
    });
  });

  it('has a Playgroups link in the primary navigation', async () => {
    stubApi();
    mount();
    const nav = screen.getByRole('navigation', { name: 'Primary' });
    expect(within(nav).getByRole('link', { name: 'Playgroups' })).toHaveAttribute(
      'href',
      '/playgroups',
    );
  });
});
