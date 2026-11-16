// S-2.1.7 m4: no test reaches the network unless it stubs fetch itself.
import { mountApp, unmountApp } from './app-harness';

jest.mock('expo-secure-store', () =>
  jest.requireActual<typeof import('./app-harness')>('./app-harness').createSecureStoreMock(),
);
jest.setTimeout(60000);

afterEach(unmountApp);

describe('the default fetch in the mobile jest setup', () => {
  it('rejects with a clear message naming the URL', async () => {
    await expect(fetch('http://localhost:8000/api/v1/health/live')).rejects.toThrow(
      'Unstubbed fetch in a unit test: http://localhost:8000/api/v1/health/live',
    );
  });

  it('is still the default after the app modules load, so an unstubbed mount makes no real request', async () => {
    const app = await mountApp('/reset/tok-1');
    await app.screen.findByTestId('reset-screen');
    await expect(fetch('http://localhost:8000/x')).rejects.toThrow(/Unstubbed fetch/);
  });
});
