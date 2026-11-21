// S-2.4.1: the playgroups screen on the More tab, through the real route tree with a stubbed fetch.
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
const ME = { id: 'g1', name: 'Friday Night', description: '', role: 'owner' };
const EMPTY = { items: [], next_cursor: null };

afterEach(async () => {
  await unmountApp();
  jest.restoreAllMocks();
});

type App = Awaited<ReturnType<typeof mountApp>>;

const submit = (app: App) =>
  app.screen.getByRole('button', { name: /^create playgroup$|^creating/i });

async function openScreen() {
  const app = await mountApp('/');
  await app.screen.findByLabelText('I have an invite');
  await app.lib.act(async () => {
    await sessionStore().getState().signIn({ accessToken: ACCESS_1, refreshToken: REFRESH_1 });
  });
  await app.fireEvent.press(await app.screen.findByLabelText('More'));
  await app.fireEvent.press(await app.screen.findByRole('button', { name: 'Playgroups' }));
  await app.screen.findByLabelText('Name');
  return app;
}

describe('the list', () => {
  it('is opened from the More tab and shows each playgroup with its role', async () => {
    stubApi({
      [PATH]: () =>
        json({
          items: [ME, { ...ME, id: 'g2', name: 'Casual', role: 'member' }],
          next_cursor: null,
        }),
    });
    const app = await openScreen();
    expect(app.screen).toHavePathname('/more/playgroups');
    expect(await app.screen.findByText('Friday Night (owner)')).toBeTruthy();
    expect(app.screen.getByText('Casual (member)')).toBeTruthy();
  });

  it('shows the empty state', async () => {
    stubApi({ [PATH]: () => json(EMPTY) });
    const app = await openScreen();
    expect(await app.screen.findByText('You are not in any playgroup yet.')).toBeTruthy();
  });

  it('shows an error with a working retry', async () => {
    let fail = true;
    stubApi({
      [PATH]: () =>
        fail ? failure(500, 'internal_error', 'boom') : json({ items: [ME], next_cursor: null }),
    });
    const app = await openScreen();
    const retry = await app.screen.findByRole('button', { name: 'Retry' });
    expect(isAnnounced(await app.screen.findByTestId('playgroups-error'))).toBe(true);
    fail = false;
    await app.fireEvent.press(retry);
    expect(await app.screen.findByText('Friday Night (owner)')).toBeTruthy();
  });

  it('loads more with the cursor', async () => {
    let page = 0;
    const api = stubApi({
      [PATH]: () => {
        page += 1;
        return page === 1
          ? json({ items: [ME], next_cursor: 'c2' })
          : json({ items: [{ ...ME, id: 'g2', name: 'Second' }], next_cursor: null });
      },
    });
    const app = await openScreen();
    await app.fireEvent.press(await app.screen.findByRole('button', { name: 'Load more' }));
    expect(await app.screen.findByText('Second (owner)')).toBeTruthy();
    expect(api.to(PATH)).toHaveLength(2);
    expect(app.screen.queryByRole('button', { name: 'Load more' })).toBeNull();
  });
});

describe('the create form', () => {
  it('rejects a short name and an over-long description without a request', async () => {
    const api = stubApi({ [PATH]: () => json(EMPTY) });
    const app = await openScreen();
    await app.fireEvent.changeText(app.screen.getByLabelText('Name'), ' ab ');
    await app.fireEvent.changeText(
      app.screen.getByLabelText('Description (optional)'),
      'x'.repeat(501),
    );
    await app.fireEvent.press(submit(app));
    expect(isAnnounced(await app.screen.findByText('Name must be 3 to 50 characters.'))).toBe(true);
    expect(app.screen.getByText('Description must be at most 500 characters.')).toBeTruthy();
    expect(api.calls.filter((c) => c.method === 'POST')).toHaveLength(0);
  });

  it('counts code points: three emoji are a valid name', async () => {
    const api = stubApi({
      [PATH]: (call) =>
        call.method === 'POST' ? json({ ...ME, name: '😀😀😀' }, 201) : json(EMPTY),
    });
    const app = await openScreen();
    await app.fireEvent.changeText(app.screen.getByLabelText('Name'), '😀😀😀');
    await app.fireEvent.press(submit(app));
    await app.screen.findByTestId('playgroup-status');
    expect(api.calls.some((c) => c.method === 'POST')).toBe(true);
  });

  it('posts the trimmed name, then clears, announces and refreshes the list', async () => {
    let created = false;
    const api = stubApi({
      [PATH]: (call) => {
        if (call.method === 'POST') {
          created = true;
          return json(ME, 201);
        }
        return json({ items: created ? [ME] : [], next_cursor: null });
      },
    });
    const app = await openScreen();
    // mountApp resets the module registry, so spy on the instance the app now uses.
    const { AccessibilityInfo } = jest.requireActual<typeof import('react-native')>('react-native');
    const announce = jest
      .spyOn(AccessibilityInfo, 'announceForAccessibility')
      .mockImplementation(() => undefined);
    await app.fireEvent.changeText(app.screen.getByLabelText('Name'), '  Friday Night ');
    await app.fireEvent.changeText(app.screen.getByLabelText('Description (optional)'), 'cube');
    await app.fireEvent.press(submit(app));
    const status = await app.screen.findByTestId('playgroup-status');
    expect(status).toHaveTextContent('Created playgroup Friday Night.');
    await app.lib.waitFor(() =>
      expect(announce).toHaveBeenCalledWith('Created playgroup Friday Night.'),
    );
    const post = api.calls.find((c) => c.method === 'POST')!;
    expect(post.body).toEqual({ name: 'Friday Night', description: 'cube' });
    expect(app.screen.getByLabelText('Name').props.value).toBe('');
    expect(await app.screen.findByText('Friday Night (owner)')).toBeTruthy();
  });

  it('does not double submit while pending', async () => {
    const gate = deferred();
    const api = stubApi({
      [PATH]: (call) => (call.method === 'POST' ? gate.promise : json(EMPTY)),
    });
    const app = await openScreen();
    await app.fireEvent.changeText(app.screen.getByLabelText('Name'), 'Friday Night');
    await app.fireEvent.press(submit(app));
    await app.lib.waitFor(() =>
      expect(submit(app).props.accessibilityState).toMatchObject({ disabled: true, busy: true }),
    );
    await app.fireEvent.press(submit(app));
    await app.lib.act(async () => {
      gate.release(json(ME, 201));
      await gate.promise;
    });
    await app.screen.findByTestId('playgroup-status');
    expect(api.calls.filter((c) => c.method === 'POST')).toHaveLength(1);
  });

  it('maps a validation_error onto the fields', async () => {
    stubApi({
      [PATH]: (call) =>
        call.method === 'POST'
          ? failure(
              422,
              'validation_error',
              'Invalid',
              {},
              {
                fields: [{ loc: ['body', 'name'], message: 'Name is taken.' }],
              },
            )
          : json(EMPTY),
    });
    const app = await openScreen();
    await app.fireEvent.changeText(app.screen.getByLabelText('Name'), 'Friday Night');
    await app.fireEvent.press(submit(app));
    expect(await app.screen.findByText('Name is taken.')).toBeTruthy();
  });

  it('shows another failure as a form error and keeps the input', async () => {
    stubApi({
      [PATH]: (call) =>
        call.method === 'POST' ? failure(500, 'internal_error', 'boom') : json(EMPTY),
    });
    const app = await openScreen();
    await app.fireEvent.changeText(app.screen.getByLabelText('Name'), 'Friday Night');
    await app.fireEvent.press(submit(app));
    const error = await app.screen.findByTestId('playgroup-form-error');
    expect(error).toHaveTextContent('Something went wrong. Try again.');
    expect(app.screen.getByLabelText('Name').props.value).toBe('Friday Night');
  });
});
