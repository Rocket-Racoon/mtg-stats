// S-2.4.1 acceptance (T-2.4.1.5, mobile): the create-playgroup screen and the list on the More tab,
// written from the story's criteria and driven through the real route tree with a stubbed network.
//
// Client/server boundary: the form counts Unicode code points after JS `trim()`; the API strips
// Unicode white space and counts code points. Emoji therefore agree. JS `trim()` strips U+FEFF but
// not U+0085, the API the opposite; those inputs are reported in the hand-off, not asserted here.
import fs from 'node:fs';
import path from 'node:path';
import {
  ACCESS_1,
  REFRESH_1,
  deferred,
  failure,
  isAnnounced,
  json,
  mountApp,
  sessionStore,
  stubApi,
  unmountApp,
} from './app-harness';

jest.mock('expo-secure-store', () =>
  jest.requireActual<typeof import('./app-harness')>('./app-harness').createSecureStoreMock(),
);
jest.setTimeout(60000);

const PATH = '/api/v1/playgroups';
const group = (name: string, role = 'member', id = `id-${name}`) => ({
  id,
  name,
  description: '',
  role,
});
const EMPTY = { items: [], next_cursor: null };

afterEach(async () => {
  await unmountApp();
  jest.restoreAllMocks();
});

type App = Awaited<ReturnType<typeof mountApp>>;

const submit = (app: App) =>
  app.screen.getByRole('button', { name: /^create playgroup$|^creating/i });
const nameInput = (app: App) => app.screen.getByLabelText('Name');
const descriptionInput = (app: App) => app.screen.getByLabelText('Description (optional)');
const posts = (api: ReturnType<typeof stubApi>) => api.calls.filter((c) => c.method === 'POST');

async function signedInApp() {
  const app = await mountApp('/');
  await app.screen.findByLabelText('I have an invite');
  await app.lib.act(async () => {
    await sessionStore().getState().signIn({ accessToken: ACCESS_1, refreshToken: REFRESH_1 });
  });
  return app;
}

/** Signs in and reaches the screen the way a user does: More tab, then the Playgroups button. */
async function openScreen() {
  const app = await signedInApp();
  await app.fireEvent.press(await app.screen.findByLabelText('More'));
  await app.fireEvent.press(await app.screen.findByRole('button', { name: 'Playgroups' }));
  await app.screen.findByLabelText('Name');
  return app;
}

/** Records the URL of every request made from now on (the harness hides query strings). */
function recordUrls() {
  const original = globalThis.fetch;
  const urls: string[] = [];
  globalThis.fetch = ((input: string | Request, init?: RequestInit) => {
    urls.push(typeof input === 'string' ? input : input.url);
    return original(input, init);
  }) as typeof fetch;
  return urls;
}

/** A fake API: POST creates (caller becomes owner), GET lists the mutable `items`. */
function fakeApi(
  items: ReturnType<typeof group>[] = [],
  post?: (body: unknown) => Response | Promise<Response>,
) {
  return stubApi({
    [PATH]: (call) => {
      if (call.method === 'POST') {
        if (post) return post(call.body);
        const created = group((call.body as { name: string }).name, 'owner');
        items.unshift(created);
        return json(created, 201);
      }
      return json({ items, next_cursor: null });
    },
  });
}

/** The list rows as the user reads them. */
const rowTexts = (app: App) =>
  app.screen
    .getAllByText(/\((owner|admin|member)\)$/)
    .map((n) => (n.props as { children: unknown }).children)
    .map((c) => (Array.isArray(c) ? c.join('') : String(c)));

describe('criterion 1: creating a playgroup through the UI', () => {
  it('is reached from the More tab by a signed-in user', async () => {
    fakeApi();
    const app = await openScreen();
    expect(app.screen).toHavePathname('/more/playgroups');
    expect(await app.screen.findByRole('header', { name: 'Create a playgroup' })).toBeTruthy();
    expect(descriptionInput(app)).toBeTruthy();
  });

  it('sends exactly one POST with the name and an empty description when none is typed', async () => {
    const api = fakeApi();
    const app = await openScreen();
    await app.fireEvent.changeText(nameInput(app), 'Friday Night');
    await app.fireEvent.press(submit(app));
    await app.screen.findByTestId('playgroup-status');
    expect(posts(api)).toHaveLength(1);
    expect(posts(api)[0]?.body).toEqual({ name: 'Friday Night', description: '' });
  });

  it('sends the description as typed', async () => {
    const api = fakeApi();
    const app = await openScreen();
    await app.fireEvent.changeText(nameInput(app), 'Friday Night');
    await app.fireEvent.changeText(descriptionInput(app), '  Commander, 4 players ');
    await app.fireEvent.press(submit(app));
    await app.screen.findByTestId('playgroup-status');
    expect(posts(api)[0]?.body).toEqual({
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
      const api = fakeApi();
      const app = await openScreen();
      await app.fireEvent.changeText(nameInput(app), name);
      await app.fireEvent.press(submit(app));
      const error = await app.screen.findByTestId('playgroup-name-error');
      expect(error).toHaveTextContent('Name must be 3 to 50 characters.');
      expect(isAnnounced(error)).toBe(true);
      expect(posts(api)).toHaveLength(0);
    });

    it.each([
      ['a 3-character name', 'a'.repeat(3)],
      ['a 50-character name', 'a'.repeat(50)],
    ])('accepts %s', async (_label, name) => {
      const api = fakeApi();
      const app = await openScreen();
      await app.fireEvent.changeText(nameInput(app), name);
      await app.fireEvent.press(submit(app));
      await app.screen.findByTestId('playgroup-status');
      expect(posts(api)).toHaveLength(1);
      expect(posts(api)[0]?.body).toEqual({ name, description: '' });
    });

    it('accepts a 500-character description', async () => {
      const api = fakeApi();
      const app = await openScreen();
      await app.fireEvent.changeText(nameInput(app), 'Friday Night');
      await app.fireEvent.changeText(descriptionInput(app), 'd'.repeat(500));
      await app.fireEvent.press(submit(app));
      await app.screen.findByTestId('playgroup-status');
      expect((posts(api)[0]?.body as { description: string }).description).toHaveLength(500);
    });

    it('rejects a 501-character description', async () => {
      const api = fakeApi();
      const app = await openScreen();
      await app.fireEvent.changeText(nameInput(app), 'Friday Night');
      await app.fireEvent.changeText(descriptionInput(app), 'd'.repeat(501));
      await app.fireEvent.press(submit(app));
      const error = await app.screen.findByTestId('playgroup-description-error');
      expect(error).toHaveTextContent('Description must be at most 500 characters.');
      expect(isAnnounced(error)).toBe(true);
      expect(posts(api)).toHaveLength(0);
    });
  });

  describe('characters are Unicode code points, like the API', () => {
    it('accepts 3 emoji as a name and sends them', async () => {
      const api = fakeApi();
      const app = await openScreen();
      await app.fireEvent.changeText(nameInput(app), '😀😀😀');
      await app.fireEvent.press(submit(app));
      await app.screen.findByTestId('playgroup-status');
      expect(posts(api)[0]?.body).toEqual({ name: '😀😀😀', description: '' });
    });

    it('rejects 2 emoji as a name', async () => {
      const api = fakeApi();
      const app = await openScreen();
      await app.fireEvent.changeText(nameInput(app), '😀😀');
      await app.fireEvent.press(submit(app));
      expect(await app.screen.findByTestId('playgroup-name-error')).toBeTruthy();
      expect(posts(api)).toHaveLength(0);
    });

    it('accepts 50 emoji and rejects 51 emoji as a name', async () => {
      const api = fakeApi();
      const app = await openScreen();
      await app.fireEvent.changeText(nameInput(app), '😀'.repeat(51));
      await app.fireEvent.press(submit(app));
      expect(await app.screen.findByTestId('playgroup-name-error')).toBeTruthy();
      expect(posts(api)).toHaveLength(0);
      await app.fireEvent.changeText(nameInput(app), '😀'.repeat(50));
      await app.fireEvent.press(submit(app));
      await app.screen.findByTestId('playgroup-status');
      expect(posts(api)).toHaveLength(1);
    });

    it('accepts a description of 500 emoji and rejects 501', async () => {
      const api = fakeApi();
      const app = await openScreen();
      await app.fireEvent.changeText(nameInput(app), 'Friday Night');
      await app.fireEvent.changeText(descriptionInput(app), '😀'.repeat(501));
      await app.fireEvent.press(submit(app));
      expect(await app.screen.findByTestId('playgroup-description-error')).toBeTruthy();
      expect(posts(api)).toHaveLength(0);
      await app.fireEvent.changeText(descriptionInput(app), '😀'.repeat(500));
      await app.fireEvent.press(submit(app));
      await app.screen.findByTestId('playgroup-status');
      expect(posts(api)).toHaveLength(1);
    });
  });

  it('tells the user it worked with the name, readies the form and lists the owner role', async () => {
    const api = fakeApi([group('Existing crew', 'member')]);
    const app = await openScreen();
    expect(await app.screen.findByText('Existing crew (member)')).toBeTruthy();
    // mountApp resets the module registry, so spy on the instance the app now uses.
    const { AccessibilityInfo } = jest.requireActual<typeof import('react-native')>('react-native');
    const announce = jest
      .spyOn(AccessibilityInfo, 'announceForAccessibility')
      .mockImplementation(() => undefined);
    await app.fireEvent.changeText(nameInput(app), 'Friday Night');
    await app.fireEvent.changeText(descriptionInput(app), 'Commander');
    await app.fireEvent.press(submit(app));
    const status = await app.screen.findByTestId('playgroup-status');
    expect(status).toHaveTextContent('Created playgroup Friday Night.');
    expect(isAnnounced(status)).toBe(true);
    await app.lib.waitFor(() =>
      expect(announce).toHaveBeenCalledWith('Created playgroup Friday Night.'),
    );
    expect(nameInput(app).props.value).toBe('');
    expect(descriptionInput(app).props.value).toBe('');
    // Without a manual reload: the list was refetched after the POST.
    expect(await app.screen.findByText('Friday Night (owner)')).toBeTruthy();
    expect(app.screen.getByText('Existing crew (member)')).toBeTruthy();
    expect(api.calls.filter((c) => c.method === 'GET').length).toBeGreaterThanOrEqual(2);
  });

  it('is ready for another playgroup right after a success', async () => {
    const api = fakeApi();
    const app = await openScreen();
    await app.fireEvent.changeText(nameInput(app), 'First crew');
    await app.fireEvent.press(submit(app));
    await app.screen.findByText('Created playgroup First crew.');
    await app.fireEvent.changeText(nameInput(app), 'Second crew');
    await app.fireEvent.press(submit(app));
    await app.screen.findByText('Created playgroup Second crew.');
    expect(posts(api).map((c) => c.body)).toEqual([
      { name: 'First crew', description: '' },
      { name: 'Second crew', description: '' },
    ]);
    expect(await app.screen.findByText('Second crew (owner)')).toBeTruthy();
    expect(app.screen.getByText('First crew (owner)')).toBeTruthy();
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
      fakeApi([], () => failure(422, 'validation_error', message, {}, fields));
      const app = await openScreen();
      await app.fireEvent.changeText(nameInput(app), 'Friday Night');
      await app.fireEvent.changeText(descriptionInput(app), 'Commander');
      await app.fireEvent.press(submit(app));
      const nameError = await app.screen.findByTestId('playgroup-name-error');
      expect(nameError).toHaveTextContent('Name is not allowed.');
      expect(isAnnounced(nameError)).toBe(true);
      const descriptionError = app.screen.getByTestId('playgroup-description-error');
      expect(descriptionError).toHaveTextContent('Description is not allowed.');
      expect(isAnnounced(descriptionError)).toBe(true);
      expect(app.screen.queryByTestId('playgroup-form-error')).toBeNull();
      expect(nameInput(app).props.value).toBe('Friday Night');
      expect(descriptionInput(app).props.value).toBe('Commander');
    },
  );

  it('does not map field errors when the code is not validation_error, whatever the message', async () => {
    // Same status, details and message a validation_error would carry: only the code differs.
    fakeApi([], () => failure(422, 'something_else', 'Invalid request.', {}, fields));
    const app = await openScreen();
    await app.fireEvent.changeText(nameInput(app), 'Friday Night');
    await app.fireEvent.press(submit(app));
    expect(await app.screen.findByTestId('playgroup-form-error')).toBeTruthy();
    expect(app.screen.queryByText('Name is not allowed.')).toBeNull();
    expect(app.screen.queryByTestId('playgroup-name-error')).toBeNull();
    expect(nameInput(app).props.value).toBe('Friday Night');
  });

  it.each([
    [500, 'internal_error', 'The server broke.'],
    [500, 'internal_error', 'A different sentence for the same code.'],
    [429, 'rate_limited', 'Too many requests.'],
  ])(
    'shows the generic form error for a %i %s (message %j) and keeps the typed values',
    async (status, code, message) => {
      const api = fakeApi([], () => failure(status, code, message));
      const app = await openScreen();
      await app.fireEvent.changeText(nameInput(app), 'Friday Night');
      await app.fireEvent.changeText(descriptionInput(app), 'Commander');
      await app.fireEvent.press(submit(app));
      const error = await app.screen.findByTestId('playgroup-form-error');
      expect(isAnnounced(error)).toBe(true);
      expect(nameInput(app).props.value).toBe('Friday Night');
      expect(descriptionInput(app).props.value).toBe('Commander');
      expect(app.screen.queryByTestId('playgroup-name-error')).toBeNull();
      expect(app.screen.queryByTestId('playgroup-status')).toBeNull();
      expect(posts(api)).toHaveLength(1);
      expect(submit(app).props.accessibilityState).toMatchObject({ disabled: false, busy: false });
    },
  );

  it('shows the same text for a 500 whatever its message (the UI switches on code, not message)', async () => {
    const texts: string[] = [];
    for (const message of ['one sentence', 'another sentence']) {
      fakeApi([], () => failure(500, 'internal_error', message));
      const app = await openScreen();
      await app.fireEvent.changeText(nameInput(app), 'Friday Night');
      await app.fireEvent.press(submit(app));
      const error = await app.screen.findByTestId('playgroup-form-error');
      texts.push(String((error.props as { children: unknown }).children));
      await unmountApp();
    }
    expect(texts[0]).toBe(texts[1]);
  });
});

describe('no double submit', () => {
  it('sends one request when pressed again while pending, and marks the button disabled and busy', async () => {
    const gate = deferred();
    const api = stubApi({
      [PATH]: (call) => (call.method === 'POST' ? gate.promise : json(EMPTY)),
    });
    const app = await openScreen();
    await app.fireEvent.changeText(nameInput(app), 'Friday Night');
    await app.fireEvent.press(submit(app));
    await app.lib.waitFor(() =>
      expect(submit(app).props.accessibilityState).toMatchObject({ disabled: true, busy: true }),
    );
    expect(app.screen.getByRole('button', { name: 'Creating…' })).toBeTruthy();
    await app.fireEvent.press(submit(app));
    await app.fireEvent.press(submit(app));
    await app.lib.act(async () => {
      gate.release(json(group('Friday Night', 'owner'), 201));
      await gate.promise;
    });
    await app.screen.findByTestId('playgroup-status');
    expect(posts(api)).toHaveLength(1);
    expect(submit(app).props.accessibilityState).toMatchObject({ disabled: false, busy: false });
  });
});

describe('accessibility', () => {
  it('gives the fields accessible names and the submit control a button role and name', async () => {
    fakeApi();
    const app = await openScreen();
    expect(nameInput(app)).toBeTruthy();
    expect(descriptionInput(app)).toBeTruthy();
    const button = submit(app);
    expect(button.props.accessibilityRole).toBe('button');
    expect(button.props.accessibilityLabel).toBe('Create playgroup');
    expect(button.props.accessibilityState).toMatchObject({ disabled: false, busy: false });
  });

  it('announces a field error as an alert when it appears and removes it once fixed', async () => {
    fakeApi();
    const app = await openScreen();
    await app.fireEvent.changeText(nameInput(app), 'ab');
    await app.fireEvent.press(submit(app));
    expect(isAnnounced(await app.screen.findByTestId('playgroup-name-error'))).toBe(true);
    await app.fireEvent.changeText(nameInput(app), 'abc');
    await app.fireEvent.press(submit(app));
    await app.screen.findByTestId('playgroup-status');
    expect(app.screen.queryByTestId('playgroup-name-error')).toBeNull();
  });
});

describe('criterion 4: the list through the UI', () => {
  it('shows every playgroup with its role', async () => {
    fakeApi([group('Crew A', 'owner'), group('Crew B', 'admin'), group('Crew C', 'member')]);
    const app = await openScreen();
    expect(await app.screen.findByText('Crew A (owner)')).toBeTruthy();
    expect(rowTexts(app)).toEqual(['Crew A (owner)', 'Crew B (admin)', 'Crew C (member)']);
  });

  it('loads more with next_cursor, appends without duplicates, then hides the control', async () => {
    stubApi({
      [PATH]: () =>
        json({
          items: [group('Crew A', 'owner'), group('Crew B', 'admin')],
          next_cursor: 'cursor-2',
        }),
    });
    const app = await openScreen();
    await app.screen.findByText('Crew A (owner)');
    // From here on every GET answers by cursor, and the URLs are recorded.
    stubApi({
      [PATH]: () =>
        json({ items: [group('Crew C', 'member'), group('Crew D', 'owner')], next_cursor: null }),
    });
    const urls = recordUrls();
    await app.fireEvent.press(await app.screen.findByRole('button', { name: 'Load more' }));
    expect(await app.screen.findByText('Crew D (owner)')).toBeTruthy();
    const listCalls = urls.filter((u) => new URL(u).pathname === PATH);
    expect(listCalls).toHaveLength(1);
    expect(new URL(listCalls[0] as string).searchParams.get('cursor')).toBe('cursor-2');
    expect(rowTexts(app)).toEqual([
      'Crew A (owner)',
      'Crew B (admin)',
      'Crew C (member)',
      'Crew D (owner)',
    ]);
    expect(app.screen.queryByRole('button', { name: 'Load more' })).toBeNull();
  });

  it('has no Load more control when next_cursor is null', async () => {
    fakeApi([group('Crew A', 'owner')]);
    const app = await openScreen();
    await app.screen.findByText('Crew A (owner)');
    expect(app.screen.queryByRole('button', { name: 'Load more' })).toBeNull();
  });

  it('shows the empty state', async () => {
    fakeApi([]);
    const app = await openScreen();
    expect(await app.screen.findByText('You are not in any playgroup yet.')).toBeTruthy();
    expect(app.screen.queryByTestId('playgroups-list')).toBeNull();
  });

  it('shows an announced error state whose retry refetches the list', async () => {
    let fail = true;
    const api = stubApi({
      [PATH]: () =>
        fail
          ? failure(429, 'rate_limited', 'The list broke.')
          : json({ items: [group('Crew A', 'owner')], next_cursor: null }),
    });
    const app = await openScreen();
    const retry = await app.screen.findByRole('button', { name: 'Retry' });
    expect(isAnnounced(await app.screen.findByTestId('playgroups-error'))).toBe(true);
    const before = api.to(PATH).length;
    fail = false;
    await app.fireEvent.press(retry);
    expect(await app.screen.findByText('Crew A (owner)')).toBeTruthy();
    expect(api.to(PATH).length).toBeGreaterThan(before);
    expect(app.screen.queryByRole('button', { name: 'Retry' })).toBeNull();
  });
});

describe('criterion 5: there is no cap', () => {
  it('lets a user who already has 25 playgroups create another, with no warning', async () => {
    const existing = Array.from({ length: 25 }, (_, i) =>
      group(`Crew ${String(i + 1).padStart(2, '0')}`, i % 3 === 0 ? 'owner' : 'member'),
    );
    const api = fakeApi(existing);
    const app = await openScreen();
    await app.screen.findByText('Crew 01 (owner)');
    expect(rowTexts(app)).toHaveLength(25);
    expect(app.screen.queryByText(/limit|maximum|too many|upgrade/i)).toBeNull();
    await app.fireEvent.changeText(nameInput(app), 'Number twenty-six');
    await app.fireEvent.press(submit(app));
    expect(await app.screen.findByText('Number twenty-six (owner)')).toBeTruthy();
    expect(posts(api)).toHaveLength(1);
    expect(rowTexts(app)).toHaveLength(26);
    expect(app.screen.queryByText(/limit|maximum|too many|upgrade/i)).toBeNull();
    expect(submit(app).props.accessibilityState).toMatchObject({ disabled: false });
  });
});

describe('signed-out visitors', () => {
  it('cannot reach /more/playgroups: they land on the sign-in stack with no form', async () => {
    const api = stubApi();
    const app = await mountApp('/more/playgroups');
    expect(await app.screen.findByLabelText('I have an invite')).toBeTruthy();
    expect(app.screen.queryByLabelText('Name')).toBeNull();
    expect(app.screen.queryByLabelText('More')).toBeNull();
    expect(api.to(PATH)).toHaveLength(0);
  });
});

describe('no direct fetch', () => {
  // guard-no-tokens-static only covers the session modules and the layout, so the files of this
  // story are checked here: they must reach the API only through the shared hooks.
  const SRC = path.resolve(__dirname, '..');
  const files = ['app/(tabs)/more/playgroups.tsx', 'playgroups/validation.ts'];
  it.each(files)('%s never calls fetch directly', (file) => {
    const code = fs
      .readFileSync(path.join(SRC, file), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^\s*\/\/.*$/gm, '');
    expect(/\bfetch\s*\(/.test(code)).toBe(false);
    expect(/\bXMLHttpRequest\b/.test(code)).toBe(false);
  });

  it('the screen uses the shared hooks', () => {
    const code = fs.readFileSync(path.join(SRC, files[0] as string), 'utf8');
    expect(code).toMatch(/useCreatePlaygroup/);
    expect(code).toMatch(/usePlaygroups/);
  });
});
