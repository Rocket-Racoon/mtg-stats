// Test helper (S-2.4.5): a stubbed API for the playgroup screens, mounted through the real route
// table and AppRoot like the other page tests.
import { render } from '@testing-library/react';
import { createMemoryRouter } from 'react-router';
import { vi } from 'vitest';
import { AppRoot } from '../providers/AppRoot';
import { routes } from '../routes';
import { useSessionStore } from '../session/store';

export const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });

export const failure = (status: number, code: string, message = 'Failed.') =>
  json({ error: { code, message, details: null, request_id: 'req-9' } }, status);

export type Role = 'owner' | 'admin' | 'member';

export const group = (id: string, name: string, role: Role = 'member') => ({
  id,
  name,
  description: '',
  role,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  joined_at: '2026-01-01T00:00:00Z',
});

export const person = (id: string, name: string, role: Role | null, avatar_color = '#336699') => ({
  id,
  display_name: name,
  avatar_color,
  is_guest: role === null,
  role,
  created_by: role === null ? 'someone' : null,
});

export interface Call {
  method: string;
  path: string;
  query: URLSearchParams;
  body: unknown;
}

export interface World {
  /** The caller's playgroups, in the API order. Returned as one page unless `pageSize` is set. */
  groups: ReturnType<typeof group>[];
  pageSize?: number;
  /** Roster per playgroup id, in the API order (members then guests). */
  players: Record<string, ReturnType<typeof person>[]>;
  playersPageSize?: number;
  /** Overrides: return a Response to take over a request. */
  handler?: (call: Call) => Response | undefined;
}

function page<T>(items: T[], size: number | undefined, cursor: string | null) {
  const start = cursor === null ? 0 : Number(cursor);
  const end = size ? start + size : items.length;
  return { items: items.slice(start, end), next_cursor: end < items.length ? String(end) : null };
}

/** Stubs fetch with a small in-memory server. Returns every call made. */
export function stubWorld(world: World): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = new Request(input, init);
      const text = await request.text();
      const url = new URL(request.url);
      const call: Call = {
        method: request.method,
        path: url.pathname,
        query: url.searchParams,
        body: text === '' ? null : (JSON.parse(text) as unknown),
      };
      calls.push(call);
      const taken = world.handler?.(call);
      if (taken) return taken;
      if (call.path === '/api/v1/playgroups' && call.method === 'GET') {
        return json(page(world.groups, world.pageSize, url.searchParams.get('cursor')));
      }
      const detail = /^\/api\/v1\/playgroups\/([^/]+)$/.exec(call.path);
      if (detail && call.method === 'GET') {
        const found = world.groups.find((g) => g.id === detail[1]);
        return found ? json(found) : failure(404, 'not_found', 'Playgroup not found.');
      }
      const players = /^\/api\/v1\/playgroups\/([^/]+)\/players$/.exec(call.path);
      if (players && call.method === 'GET') {
        const roster = world.players[players[1] ?? ''];
        if (!roster) return failure(404, 'not_found');
        return json(page(roster, world.playersPageSize, url.searchParams.get('cursor')));
      }
      if (call.path === '/api/v1/auth/refresh') return failure(401, 'invalid_refresh');
      return failure(404, 'not_found');
    }),
  );
  return calls;
}

/** Mounts the whole app at `path`, signed in. */
export function mountApp(path: string) {
  useSessionStore.setState({ status: 'signed_in', accessToken: 'token' }); // secrets-check: allow
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(<AppRoot router={router} />);
  return router;
}
