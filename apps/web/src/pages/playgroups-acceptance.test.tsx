// S-2.4.1 acceptance (T-2.4.1.4, web): the create-playgroup form and the playgroups list, written
// from the story's criteria and driven through the real route table with a stubbed network.
//
// Client/server boundary (the API validates with pydantic: strip Unicode white space, then count
// code points): the form counts code points after JS `trim()`, so emoji agree with the API. JS
// `trim()` and the API's strip differ on U+FEFF (JS strips it, the API does not) and U+0085 (the
// API strips it, JS does not); those inputs are reported in the story hand-off, not asserted here.
import fs from 'node:fs';
import path from 'node:path';
import { StrictMode } from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AppRoot } from '../providers/AppRoot';
import { routes } from '../routes';
import { useSessionStore } from '../session/store';
import { resetToasts } from '../ui/toasts';

interface Call {
  path: string;
  method: string;
  query: URLSearchParams;
  body: unknown;
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
const failure = (status: number, code: string, message: string, details: unknown = null) =>
  json({ error: { code, message, details, request_id: 'req-1' } }, status);

const group = (name: string, role = 'member', id = `id-${name}`) => ({
  id,
  name,
  description: '',
  role,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  joined_at: '2026-01-01T00:00:00Z',
});

type PostHandler = (call: Call) => Response | Promise<Response>;
type GetHandler = (call: Call) => Response | Promise<Response>;

/** A fake API. POST creates (caller becomes owner); GET lists the mutable `items`. */
function stubApi(
  options: { items?: ReturnType<typeof group>[]; post?: PostHandler; get?: GetHandler } = {},
) {
  const calls: Call[] = [];
  const items = options.items ?? [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = new Request(input, init);
      const text = await request.text();
      const url = new URL(request.url);
      const call: Call = {
        path: url.pathname,
        method: request.method,
        query: url.searchParams,
        body: text === '' ? null : (JSON.parse(text) as unknown),
      };
      calls.push(call);
      if (call.path === '/api/v1/playgroups' && call.method === 'GET') {
        return options.get ? options.get(call) : json({ items, next_cursor: null });
      }
      if (call.path === '/api/v1/playgroups' && call.method === 'POST') {
        if (options.post) return options.post(call);
        const body = call.body as { name: string };
        const created = group(body.name, 'owner');
        items.unshift(created);
        return json(created, 201);
      }
      return failure(401, 'invalid_refresh', 'Session expired.');
    }),
  );
  return {
    calls,
    posts: () => calls.filter((c) => c.method === 'POST' && c.path === '/api/v1/playgroups'),
    gets: () => calls.filter((c) => c.method === 'GET' && c.path === '/api/v1/playgroups'),
  };
}

function mount(url = '/playgroups') {
  const router = createMemoryRouter(routes, { initialEntries: [url] });
  render(
    <StrictMode>
      <AppRoot router={router} />
    </StrictMode>,
  );
  return router;
}

const nameField = () => screen.getByRole('textbox', { name: 'Name' });
const descriptionField = () => screen.getByRole('textbox', { name: 'Description (optional)' });
const submit = () => screen.getByRole('button', { name: /^create playgroup$|^creating/i });
const listReady = () => screen.findByRole('heading', { name: 'Your playgroups' });
/** The "Your playgroups" region, so the navigation's own list items are not counted. */
const yourList = () => within(screen.getByRole('region', { name: 'Your playgroups' }));

/** Pastes text (typing 500 characters one key at a time is needlessly slow). */
async function paste(user: ReturnType<typeof userEvent.setup>, field: HTMLElement, text: string) {
  await user.click(field);
  await user.paste(text);
}

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

describe('criterion 1: creating a playgroup through the UI', () => {
  it('is reached from the primary navigation by a signed-in user', async () => {
    stubApi();
    const router = mount('/');
    const user = userEvent.setup();
    const nav = await screen.findByRole('navigation', { name: 'Primary' });
    await user.click(within(nav).getByRole('link', { name: 'Playgroups' }));
    expect(router.state.location.pathname).toBe('/playgroups');
    expect(await screen.findByRole('heading', { name: 'Create a playgroup' })).toBeInTheDocument();
    expect(nameField()).toBeInTheDocument();
  });

  it('sends exactly one POST with the name and an empty description when none is typed', async () => {
    const api = stubApi();
    const user = userEvent.setup();
    mount();
    await listReady();
    await user.type(nameField(), 'Friday Night');
    await user.click(submit());
    await screen.findByText('Created playgroup Friday Night.');
    expect(api.posts()).toHaveLength(1);
    expect(api.posts()[0]?.body).toEqual({ name: 'Friday Night', description: '' });
  });

  it('sends the description as typed', async () => {
    const api = stubApi();
    const user = userEvent.setup();
    mount();
    await listReady();
    await user.type(nameField(), 'Friday Night');
    await user.type(descriptionField(), '  Commander, 4 players ');
    await user.click(submit());
    await screen.findByText('Created playgroup Friday Night.');
    expect(api.posts()[0]?.body).toEqual({
      name: 'Friday Night',
      description: '  Commander, 4 players ',
    });
  });

  describe('boundaries are enforced before any request is sent', () => {
    it.each([
      ['an empty name', ''],
      ['a whitespace-only name', '     '],
      ['a 2-character name', 'ab'],
      ['a 51-character name', 'a'.repeat(51)],
    ])('rejects %s', async (_label, name) => {
      const api = stubApi();
      const user = userEvent.setup();
      mount();
      await listReady();
      if (name) await paste(user, nameField(), name);
      await user.click(submit());
      expect(nameField()).toHaveAttribute('aria-invalid', 'true');
      expect(nameField()).toHaveAccessibleDescription('Name must be 3 to 50 characters.');
      expect(api.posts()).toHaveLength(0);
    });

    it.each([
      ['a 3-character name', 'a'.repeat(3)],
      ['a 50-character name', 'a'.repeat(50)],
    ])('accepts %s', async (_label, name) => {
      const api = stubApi();
      const user = userEvent.setup();
      mount();
      await listReady();
      await paste(user, nameField(), name);
      await user.click(submit());
      await screen.findByText(`Created playgroup ${name}.`);
      expect(api.posts()).toHaveLength(1);
      expect(api.posts()[0]?.body).toEqual({ name, description: '' });
    });

    it('accepts a 500-character description', async () => {
      const api = stubApi();
      const user = userEvent.setup();
      mount();
      await listReady();
      await user.type(nameField(), 'Friday Night');
      await paste(user, descriptionField(), 'd'.repeat(500));
      await user.click(submit());
      await screen.findByText('Created playgroup Friday Night.');
      expect((api.posts()[0]?.body as { description: string }).description).toHaveLength(500);
    });

    it('rejects a 501-character description and focuses it', async () => {
      const api = stubApi();
      const user = userEvent.setup();
      mount();
      await listReady();
      await user.type(nameField(), 'Friday Night');
      await paste(user, descriptionField(), 'd'.repeat(501));
      await user.click(submit());
      expect(descriptionField()).toHaveAttribute('aria-invalid', 'true');
      expect(descriptionField()).toHaveAccessibleDescription(
        'Description must be at most 500 characters.',
      );
      expect(descriptionField()).toHaveFocus();
      expect(api.posts()).toHaveLength(0);
    });
  });

  describe('characters are Unicode code points, like the API', () => {
    it('accepts 3 emoji as a name (3 code points, 6 UTF-16 units) and sends them', async () => {
      const api = stubApi();
      const user = userEvent.setup();
      mount();
      await listReady();
      await paste(user, nameField(), '😀😀😀');
      await user.click(submit());
      await screen.findByText('Created playgroup 😀😀😀.');
      expect(api.posts()[0]?.body).toEqual({ name: '😀😀😀', description: '' });
    });

    it('rejects 2 emoji as a name', async () => {
      const api = stubApi();
      const user = userEvent.setup();
      mount();
      await listReady();
      await paste(user, nameField(), '😀😀');
      await user.click(submit());
      expect(nameField()).toHaveAttribute('aria-invalid', 'true');
      expect(api.posts()).toHaveLength(0);
    });

    it('accepts 50 emoji and rejects 51 emoji as a name', async () => {
      const api = stubApi();
      const user = userEvent.setup();
      mount();
      await listReady();
      await paste(user, nameField(), '😀'.repeat(51));
      await user.click(submit());
      expect(nameField()).toHaveAttribute('aria-invalid', 'true');
      expect(api.posts()).toHaveLength(0);
      await user.clear(nameField());
      await paste(user, nameField(), '😀'.repeat(50));
      await user.click(submit());
      await waitFor(() => {
        expect(api.posts()).toHaveLength(1);
      });
    });

    it('accepts a description of 500 emoji and rejects 501', async () => {
      const api = stubApi();
      const user = userEvent.setup();
      mount();
      await listReady();
      await user.type(nameField(), 'Friday Night');
      await paste(user, descriptionField(), '😀'.repeat(501));
      await user.click(submit());
      expect(descriptionField()).toHaveAttribute('aria-invalid', 'true');
      expect(api.posts()).toHaveLength(0);
      await user.clear(descriptionField());
      await paste(user, descriptionField(), '😀'.repeat(500));
      await user.click(submit());
      await waitFor(() => {
        expect(api.posts()).toHaveLength(1);
      });
    });
  });

  it('tells the user it worked with the name, readies the form and lists the owner role', async () => {
    const api = stubApi({ items: [group('Existing crew', 'member')] });
    const user = userEvent.setup();
    mount();
    await screen.findByText('Existing crew (member)');
    await user.type(nameField(), 'Friday Night');
    await user.type(descriptionField(), 'Commander');
    await user.click(submit());
    expect(await screen.findByText('Created playgroup Friday Night.')).toBeInTheDocument();
    expect(nameField()).toHaveValue('');
    expect(descriptionField()).toHaveValue('');
    expect(nameField()).toHaveFocus();
    expect(nameField()).not.toHaveAttribute('aria-invalid');
    // Appears without a manual reload: the list was refetched after the POST.
    expect(await screen.findByText('Friday Night (owner)')).toBeInTheDocument();
    expect(screen.getByText('Existing crew (member)')).toBeInTheDocument();
    expect(api.gets().length).toBeGreaterThanOrEqual(2);
  });

  it('is ready for another playgroup right after a success', async () => {
    const api = stubApi();
    const user = userEvent.setup();
    mount();
    await listReady();
    await user.type(nameField(), 'First crew');
    await user.click(submit());
    await screen.findByText('Created playgroup First crew.');
    await user.type(nameField(), 'Second crew');
    await user.click(submit());
    await screen.findByText('Created playgroup Second crew.');
    expect(api.posts().map((c) => c.body)).toEqual([
      { name: 'First crew', description: '' },
      { name: 'Second crew', description: '' },
    ]);
    expect(await screen.findByText('Second crew (owner)')).toBeInTheDocument();
    expect(screen.getByText('First crew (owner)')).toBeInTheDocument();
  });
});

describe('server errors are handled by code', () => {
  const fields = {
    fields: [
      { loc: ['body', 'name'], message: 'Name is not allowed.' },
      { loc: ['body', 'description'], message: 'Description is not allowed.' },
    ],
  };

  it.each([['Invalid request.'], ['Completely different wording that no client could match on.']])(
    'maps a 422 validation_error (message %j) onto the matching fields',
    async (message) => {
      stubApi({ post: () => failure(422, 'validation_error', message, fields) });
      const user = userEvent.setup();
      mount();
      await listReady();
      await user.type(nameField(), 'Friday Night');
      await user.type(descriptionField(), 'Commander');
      await user.click(submit());
      await waitFor(() => {
        expect(nameField()).toHaveAccessibleDescription('Name is not allowed.');
      });
      expect(nameField()).toHaveAttribute('aria-invalid', 'true');
      expect(descriptionField()).toHaveAttribute('aria-invalid', 'true');
      expect(descriptionField()).toHaveAccessibleDescription('Description is not allowed.');
      // Announced: each message sits in an alert region.
      const alerts = screen.getAllByRole('alert').map((a) => a.textContent);
      expect(alerts).toEqual(
        expect.arrayContaining(['Name is not allowed.', 'Description is not allowed.']),
      );
      expect(nameField()).toHaveFocus();
      expect(nameField()).toHaveValue('Friday Night');
      expect(descriptionField()).toHaveValue('Commander');
    },
  );

  it('does not map field errors when the code is not validation_error, whatever the message', async () => {
    // Same status, same details and the same message a validation_error would carry: only the
    // code differs, so a client that switched on the message would wrongly mark the fields.
    stubApi({ post: () => failure(422, 'something_else', 'Invalid request.', fields) });
    const user = userEvent.setup();
    mount();
    await listReady();
    await user.type(nameField(), 'Friday Night');
    await user.click(submit());
    expect(await screen.findByText('Invalid request.')).toBeInTheDocument();
    expect(nameField()).not.toHaveAttribute('aria-invalid');
    expect(screen.queryByText('Name is not allowed.')).not.toBeInTheDocument();
    expect(nameField()).toHaveValue('Friday Night');
  });

  it.each([
    [500, 'internal_error', 'The server broke.'],
    [500, 'internal_error', 'A different sentence for the same code.'],
    [429, 'rate_limited', 'Too many requests.'],
  ])(
    'shows the generic error surface for a %i %s (message %j) and keeps the typed values',
    async (status, code, message) => {
      const api = stubApi({ post: () => failure(status, code, message) });
      const user = userEvent.setup();
      mount();
      await listReady();
      await user.type(nameField(), 'Friday Night');
      await user.type(descriptionField(), 'Commander');
      await user.click(submit());
      expect(await screen.findByText(message)).toBeInTheDocument();
      expect(nameField()).toHaveValue('Friday Night');
      expect(descriptionField()).toHaveValue('Commander');
      expect(nameField()).not.toHaveAttribute('aria-invalid');
      expect(descriptionField()).not.toHaveAttribute('aria-invalid');
      expect(screen.queryByText(/^Created playgroup/)).not.toBeInTheDocument();
      expect(api.posts()).toHaveLength(1);
      // The form is usable again.
      expect(submit()).toBeEnabled();
    },
  );
});

describe('no double submit', () => {
  it('sends one request when activated again while pending (click, Enter and a raw submit)', async () => {
    let release: (() => void) | undefined;
    const api = stubApi({
      post: (call) =>
        new Promise<Response>((resolve) => {
          release = () => {
            resolve(json(group((call.body as { name: string }).name, 'owner'), 201));
          };
        }),
    });
    const user = userEvent.setup();
    mount();
    await listReady();
    await user.type(nameField(), 'Friday Night');
    await user.click(submit());
    await waitFor(() => {
      expect(submit()).toBeDisabled();
    });
    await user.click(submit());
    await user.type(nameField(), '{Enter}');
    fireEvent.submit(nameField().closest('form') as HTMLFormElement);
    expect(api.posts()).toHaveLength(1);
    release?.();
    await screen.findByText('Created playgroup Friday Night.');
    expect(api.posts()).toHaveLength(1);
  });
});

describe('accessibility and keyboard use', () => {
  it('gives both fields accessible names and the form a labelled region', async () => {
    stubApi();
    mount();
    await listReady();
    expect(nameField()).toBeInTheDocument();
    expect(descriptionField().tagName).toBe('TEXTAREA');
    expect(screen.getByRole('button', { name: 'Create playgroup' })).toBeInTheDocument();
  });

  it('works with the keyboard only: tab order, Enter submits, focus returns to the name', async () => {
    const api = stubApi();
    const user = userEvent.setup();
    mount();
    await listReady();
    nameField().focus();
    await user.keyboard('Friday Night');
    await user.tab();
    expect(descriptionField()).toHaveFocus();
    await user.keyboard('Commander');
    await user.tab();
    expect(submit()).toHaveFocus();
    await user.tab({ shift: true });
    expect(descriptionField()).toHaveFocus();
    await user.tab({ shift: true });
    expect(nameField()).toHaveFocus();
    await user.keyboard('{Enter}'); // Enter from the name field submits the form
    await screen.findByText('Created playgroup Friday Night.');
    expect(api.posts()).toHaveLength(1);
    expect(api.posts()[0]?.body).toEqual({ name: 'Friday Night', description: 'Commander' });
    expect(nameField()).toHaveFocus();
  });

  it('submits with Enter on the focused button, and focus lands on the invalid field after an error', async () => {
    const api = stubApi();
    const user = userEvent.setup();
    mount();
    await listReady();
    nameField().focus();
    await user.keyboard('ab');
    await user.tab();
    await user.tab();
    expect(submit()).toHaveFocus();
    await user.keyboard('{Enter}');
    expect(nameField()).toHaveFocus();
    expect(nameField()).toHaveAccessibleDescription('Name must be 3 to 50 characters.');
    expect(api.posts()).toHaveLength(0);
    await user.keyboard('c');
    await user.keyboard('{Enter}');
    await screen.findByText('Created playgroup abc.');
    expect(api.posts()).toHaveLength(1);
  });

  it('clears the invalid state once the input is valid and submitted', async () => {
    stubApi();
    const user = userEvent.setup();
    mount();
    await listReady();
    await user.type(nameField(), 'ab');
    await user.click(submit());
    expect(nameField()).toHaveAttribute('aria-invalid', 'true');
    await user.type(nameField(), 'c');
    await user.click(submit());
    await screen.findByText('Created playgroup abc.');
    expect(nameField()).not.toHaveAttribute('aria-invalid');
    expect(screen.queryByText('Name must be 3 to 50 characters.')).not.toBeInTheDocument();
  });

  it('announces the success message in a status region', async () => {
    stubApi();
    const user = userEvent.setup();
    mount();
    await listReady();
    await user.type(nameField(), 'Friday Night');
    await user.click(submit());
    const message = await screen.findByText('Created playgroup Friday Night.');
    expect(message).toHaveAttribute('role', 'status');
  });
});

describe('criterion 4: the list through the UI', () => {
  it('shows every playgroup with its role', async () => {
    stubApi({
      items: [group('Crew A', 'owner'), group('Crew B', 'admin'), group('Crew C', 'member')],
    });
    mount();
    expect(await screen.findByText('Crew A (owner)')).toBeInTheDocument();
    expect(screen.getByText('Crew B (admin)')).toBeInTheDocument();
    expect(screen.getByText('Crew C (member)')).toBeInTheDocument();
    expect(yourList().getAllByRole('listitem')).toHaveLength(3);
  });

  it('loads more with next_cursor, appends without duplicates, then hides the control', async () => {
    const api = stubApi({
      get: (call) =>
        call.query.get('cursor') === 'cursor-2'
          ? json({
              items: [group('Crew C', 'member'), group('Crew D', 'owner')],
              next_cursor: null,
            })
          : json({
              items: [group('Crew A', 'owner'), group('Crew B', 'admin')],
              next_cursor: 'cursor-2',
            }),
    });
    const user = userEvent.setup();
    mount();
    await screen.findByText('Crew A (owner)');
    expect(api.gets()[0]?.query.get('cursor')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Load more' }));
    expect(await screen.findByText('Crew D (owner)')).toBeInTheDocument();
    // StrictMode (dev only) may repeat the first page's request, so count the cursor requests.
    const cursors = api.gets().map((g) => g.query.get('cursor'));
    expect(cursors.filter((c) => c !== null)).toEqual(['cursor-2']);
    expect(cursors.at(-1)).toBe('cursor-2');
    const rows = yourList()
      .getAllByRole('listitem')
      .map((li) => li.textContent);
    expect(rows).toEqual(['Crew A (owner)', 'Crew B (admin)', 'Crew C (member)', 'Crew D (owner)']);
    expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument();
  });

  it('has no Load more control when next_cursor is null', async () => {
    stubApi({ items: [group('Crew A', 'owner')] });
    mount();
    await screen.findByText('Crew A (owner)');
    expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument();
  });

  it('shows the empty state', async () => {
    stubApi({ items: [] });
    mount();
    expect(await screen.findByText('You are not in any playgroup yet.')).toBeInTheDocument();
    expect(yourList().queryByRole('list')).not.toBeInTheDocument();
  });

  it('shows an error state whose retry refetches the list', async () => {
    let fail = true;
    const api = stubApi({
      get: () =>
        fail
          ? failure(429, 'rate_limited', 'The list broke.')
          : json({ items: [group('Crew A', 'owner')], next_cursor: null }),
    });
    const user = userEvent.setup();
    mount();
    const alert = await screen.findByText(/The list broke\./);
    expect(alert.closest('[role="alert"]')).not.toBeNull();
    const before = api.gets().length;
    fail = false;
    await user.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Crew A (owner)')).toBeInTheDocument();
    expect(api.gets().length).toBeGreaterThan(before);
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
  });
});

describe('criterion 5: there is no cap', () => {
  it('lets a user who already has 25 playgroups create another, with no warning', async () => {
    const existing = Array.from({ length: 25 }, (_, i) =>
      group(`Crew ${String(i + 1).padStart(2, '0')}`, i % 3 === 0 ? 'owner' : 'member'),
    );
    const api = stubApi({ items: existing });
    const user = userEvent.setup();
    mount();
    await screen.findByText('Crew 01 (owner)');
    expect(yourList().getAllByRole('listitem')).toHaveLength(25);
    expect(screen.queryByText(/limit|maximum|too many|upgrade/i)).not.toBeInTheDocument();
    await user.type(nameField(), 'Number twenty-six');
    await user.click(submit());
    expect(await screen.findByText('Number twenty-six (owner)')).toBeInTheDocument();
    expect(api.posts()).toHaveLength(1);
    expect(yourList().getAllByRole('listitem')).toHaveLength(26);
    expect(screen.queryByText(/limit|maximum|too many|upgrade/i)).not.toBeInTheDocument();
    expect(submit()).toBeEnabled();
  });
});

describe('signed-out visitors', () => {
  it('are redirected to /sign-in?next=/playgroups and see no form', async () => {
    useSessionStore.setState({ status: 'signed_out', accessToken: null });
    const api = stubApi();
    const router = mount('/playgroups');
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/sign-in');
    });
    expect(router.state.location.search).toBe('?next=/playgroups');
    expect(screen.queryByRole('textbox', { name: 'Name' })).not.toBeInTheDocument();
    expect(api.gets()).toHaveLength(0);
    expect(api.posts()).toHaveLength(0);
  });
});

describe('no direct fetch', () => {
  // ESLint forbids no `fetch` global in apps/web, and the mobile static guard covers other files,
  // so the files of this story are checked here: they must reach the API only through the hooks.
  const root = path.resolve(__dirname, '..', '..', '..', '..');
  const files = [
    'apps/web/src/pages/PlaygroupsPage.tsx',
    'packages/api-client/src/hooks/playgroups.ts',
  ];
  it.each(files)('%s never calls fetch directly', (file) => {
    const code = fs
      .readFileSync(path.join(root, file), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^\s*\/\/.*$/gm, '');
    expect(/\bfetch\s*\(/.test(code)).toBe(false);
    expect(/\bXMLHttpRequest\b/.test(code)).toBe(false);
  });

  it('PlaygroupsPage uses the shared hooks', () => {
    const code = fs.readFileSync(path.join(root, files[0] as string), 'utf8');
    expect(code).toMatch(/useCreatePlaygroup/);
    expect(code).toMatch(/usePlaygroups/);
  });
});
