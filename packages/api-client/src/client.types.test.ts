// AC1 (type level): every operation's path, parameters, body and response are type-checked.
// The `@ts-expect-error` lines fail `tsc --noEmit` (the package typecheck) if the call compiles.
import { describe, it } from 'vitest';
import { createApiClient } from './index';

interface TypedPaths {
  '/api/v1/decks': {
    get: {
      parameters: { query?: { limit?: number }; header?: never; path?: never };
      requestBody?: never;
      responses: {
        200: {
          headers: Record<string, unknown>;
          content: { 'application/json': { items: { id: string }[] } };
        };
      };
    };
    post: {
      parameters: { query?: never; header?: never; path?: never };
      requestBody: { content: { 'application/json': { name: string } } };
      responses: {
        201: {
          headers: Record<string, unknown>;
          content: { 'application/json': { id: string; name: string } };
        };
      };
    };
  };
  '/api/v1/decks/{deck_id}': {
    get: {
      parameters: { query?: never; header?: never; path: { deck_id: string } };
      requestBody?: never;
      responses: {
        200: {
          headers: Record<string, unknown>;
          content: { 'application/json': { id: string } };
        };
      };
    };
  };
}

const api = createApiClient<TypedPaths>({ baseUrl: 'https://api.test' });
// Never invoked: the calls are only type-checked.
const typeOnly = (fn: () => unknown): void => void fn;

describe('compile-time checks of the typed client', () => {
  it('rejects a path that is not in the spec', () => {
    typeOnly(() => {
      // @ts-expect-error unknown path
      return api.GET('/api/v1/nope');
    });
  });

  it('rejects a path used with a method the spec does not define', () => {
    typeOnly(() => {
      // @ts-expect-error /decks/{deck_id} has no POST
      return api.POST('/api/v1/decks/{deck_id}', { params: { path: { deck_id: 'x' } } });
    });
  });

  it('requires path params', () => {
    typeOnly(() => {
      // @ts-expect-error missing params.path
      return api.GET('/api/v1/decks/{deck_id}');
    });
  });

  it('rejects a path param of the wrong name or type', () => {
    typeOnly(() => {
      // @ts-expect-error wrong path param name
      return api.GET('/api/v1/decks/{deck_id}', { params: { path: { id: 'x' } } });
    });
    typeOnly(() => {
      // @ts-expect-error path param must be a string
      return api.GET('/api/v1/decks/{deck_id}', { params: { path: { deck_id: 1 } } });
    });
  });

  it('rejects a query param of the wrong type', () => {
    typeOnly(() => {
      // @ts-expect-error limit must be a number
      return api.GET('/api/v1/decks', { params: { query: { limit: 'five' } } });
    });
  });

  it('requires the request body and checks its shape', () => {
    typeOnly(() => {
      // @ts-expect-error missing body
      return api.POST('/api/v1/decks');
    });
    typeOnly(() => {
      // @ts-expect-error body.name must be a string
      return api.POST('/api/v1/decks', { body: { name: 5 } });
    });
    typeOnly(() => {
      // @ts-expect-error body is missing name
      return api.POST('/api/v1/decks', { body: {} });
    });
  });

  it('rejects reading a field that is not in the response', () => {
    typeOnly(async () => {
      const deck = await api.GET('/api/v1/decks/{deck_id}', {
        params: { path: { deck_id: 'x' } },
      });
      // @ts-expect-error `nope` is not a field of the response
      return deck.nope;
    });
  });

  it('accepts correct calls', () => {
    typeOnly(() => api.GET('/api/v1/decks', { params: { query: { limit: 1 } } }));
    typeOnly(() => api.POST('/api/v1/decks', { body: { name: 'x' } }));
  });
});
