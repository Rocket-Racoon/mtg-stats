/* eslint-disable @typescript-eslint/no-require-imports */
// Shared harness for the S-2.1.7 mobile auth tests (not a test file: jest only runs *.test.ts(x)).
//
// Why modules are reset per mount: the session store restores once per app launch (a guard against
// double runs), so every scenario needs a "fresh launch": a new module registry, a new secure-store
// mock and a new router. React Native Testing Library's auto-cleanup registers an afterEach at import
// time, which cannot happen inside a running test, so auto-cleanup is switched off here and
// `unmountApp()` (call it from afterEach) does the cleanup.
import fs from 'node:fs';
import path from 'node:path';

process.env.RNTL_SKIP_AUTO_CLEANUP = 'true';

export const ACCESS_1 = 'access-token-1'; // secrets-check: allow
export const REFRESH_1 = 'refresh-token-1'; // secrets-check: allow
export const ACCESS_2 = 'access-token-2'; // secrets-check: allow
export const REFRESH_2 = 'refresh-token-2'; // secrets-check: allow

export type Lib = typeof import('expo-router/testing-library');

export interface SecureStoreMock {
  getItemAsync: jest.Mock<Promise<string | null>, [string]>;
  setItemAsync: jest.Mock<Promise<void>, [string, string]>;
  deleteItemAsync: jest.Mock<Promise<void>, [string]>;
  __control: {
    failRead: boolean;
    failWrite: boolean;
    failDelete: boolean;
    /** Pre-existing values, found by role so the tests do not depend on the exact key names. */
    seeded: { access: string | null; refresh: string | null };
  };
}

let current: Lib | null = null;
let stubbedFetch: typeof fetch | null = null;
let originalFetch: typeof fetch | null = null;

/**
 * Expo's winter runtime re-installs its own fetch on globalThis when the app modules load, which
 * would replace a stub made before the load. So the stub is (re)assigned after the modules load.
 */
function installFetch() {
  if (!stubbedFetch) return;
  originalFetch ??= globalThis.fetch;
  Object.defineProperty(globalThis, 'fetch', {
    value: stubbedFetch,
    writable: true,
    configurable: true,
  });
}

/** Starts the real route tree at `url` as a fresh app launch. */
export async function mountApp(url: string, configure?: (store: SecureStoreMock) => void) {
  jest.resetModules();
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  const lib = require('expo-router/testing-library') as Lib;
  const secureStore = require('expo-secure-store') as SecureStoreMock;
  const { api } = require('../api') as { api: import('@mtg-stats/api-client').ApiClient };
  installFetch();
  // Navigation in the full route tree can be slow under a parallel run: wait generously. A wait
  // returns as soon as the element appears, so this only costs time when something is wrong.
  lib.configure({ asyncUtilTimeout: 15000 });
  configure?.(secureStore);
  current = lib;
  await lib.renderRouter('./src/app', { initialUrl: url });
  return { lib, screen: lib.screen, fireEvent: lib.fireEvent, secureStore, api };
}

export async function unmountApp() {
  if (originalFetch) {
    Object.defineProperty(globalThis, 'fetch', {
      value: originalFetch,
      writable: true,
      configurable: true,
    });
  }
  stubbedFetch = null;
  const lib = current;
  current = null;
  if (lib) await lib.cleanup();
}

/** The `expo-secure-store` module as an in-memory fake that can fail on demand. */
export function createSecureStoreMock(): SecureStoreMock {
  const data = new Map<string, string>();
  const control: SecureStoreMock['__control'] = {
    failRead: false,
    failWrite: false,
    failDelete: false,
    seeded: { access: null, refresh: null },
  };
  const role = (key: string) =>
    /refresh/i.test(key) ? 'refresh' : /access/i.test(key) ? 'access' : null;
  return {
    getItemAsync: jest.fn(async (key: string) => {
      if (control.failRead) throw new Error('keychain read failed');
      const stored = data.get(key);
      if (stored !== undefined) return stored;
      const r = role(key);
      return r ? control.seeded[r] : null;
    }),
    setItemAsync: jest.fn(async (key: string, value: string) => {
      if (control.failWrite) throw new Error('keychain write failed');
      data.set(key, value);
    }),
    deleteItemAsync: jest.fn(async (key: string) => {
      if (control.failDelete) throw new Error('keychain delete failed');
      data.delete(key);
      const r = role(key);
      if (r) control.seeded[r] = null;
    }),
    __control: control,
  };
}

export interface Call {
  path: string;
  method: string;
  headers: Headers;
  body: any; // eslint-disable-line @typescript-eslint/no-explicit-any
}
export type Handler = (call: Call) => Response | Promise<Response>;

export const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json', ...headers },
  });

export const failure = (
  status: number,
  code: string,
  message: string,
  headers: Record<string, string> = {},
  details: unknown = null,
) => json({ error: { code, message, details, request_id: 'req-1' } }, status, headers);

export const tokens = (access = ACCESS_1, refresh = REFRESH_1, status = 200) =>
  json(
    { access_token: access, refresh_token: refresh, token_type: 'bearer', expires_in: 900 },
    status,
  );

/**
 * Replaces global fetch so no test reaches the network. `handlers` is keyed by URL path; refresh
 * answers 401 and every other path 200 `{}` unless a handler says otherwise.
 */
export function stubApi(handlers: Record<string, Handler> = {}) {
  const calls: Call[] = [];
  const stub = jest.fn(async (input: string | Request, init?: RequestInit) => {
    const request = typeof input === 'string' ? new Request(input, init) : input.clone();
    const text = await request.text();
    const call: Call = {
      path: new URL(request.url).pathname,
      method: request.method,
      headers: request.headers,
      body: text === '' ? null : JSON.parse(text),
    };
    calls.push(call);
    const handler = handlers[call.path];
    if (handler) return handler(call);
    if (call.path === '/api/v1/auth/refresh') {
      return failure(401, 'invalid_refresh', 'Session expired.');
    }
    return json({});
  });
  stubbedFetch = stub as unknown as typeof fetch;
  installFetch();
  return { calls, to: (p: string) => calls.filter((c) => c.path === p) };
}

export const LOGIN = '/api/v1/auth/login';
export const REGISTER = '/api/v1/auth/register';
export const REFRESH = '/api/v1/auth/refresh';
export const PREVIEW = '/api/v1/invites/preview';

/** True when the node, or an ancestor, is announced as an alert / polite live region. */
interface AnyNode {
  props: Record<string, unknown>;
  parent: AnyNode | null;
}

export function isAnnounced(node: AnyNode | null): boolean {
  for (let n = node; n; n = n.parent) {
    const p = n.props ?? {};
    if (
      p.accessibilityRole === 'alert' ||
      p.role === 'alert' ||
      p.accessibilityLiveRegion === 'polite'
    ) {
      return true;
    }
  }
  return false;
}

const SRC = path.resolve(__dirname, '..');

function walk(dir: string, out: string[]) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === '__tests__' || entry.name === 'node_modules') continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(full, out);
    else if (/\.tsx?$/.test(entry.name)) out.push(full);
  }
}

/** Every non-test source file under src/. */
export function sourceFiles(): string[] {
  const out: string[] = [];
  walk(SRC, out);
  return out;
}
