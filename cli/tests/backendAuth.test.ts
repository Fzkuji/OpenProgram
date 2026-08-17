import { spawn, spawnSync } from 'child_process';
import { fileURLToPath } from 'url';

import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import WebSocket from 'ws';

import {
  backendAuthHeaders,
  backendBase,
  backendFetch,
  isBackendUrl,
  webUiUrls,
} from '../src/utils/backend.js';

const TOKEN = 'AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8';

function withBackend(port = 45231): string {
  const origin = `http://127.0.0.1:${port}`;
  process.env.OPENPROGRAM_BACKEND_URL = origin;
  process.env.OPENPROGRAM_BACKEND_ORIGIN = `http://localhost:${port}`;
  process.env.OPENPROGRAM_BACKEND_TOKEN = TOKEN;
  return origin;
}

afterEach(() => {
  delete process.env.OPENPROGRAM_BACKEND_URL;
  delete process.env.OPENPROGRAM_BACKEND_ORIGIN;
  delete process.env.OPENPROGRAM_BACKEND_TOKEN;
  delete process.env.OPENPROGRAM_WS;
  vi.unstubAllGlobals();
});

/** Capture the headers backendFetch actually sends. */
function captureFetch(): { calls: Array<{ url: string; headers: Headers }> } {
  const calls: Array<{ url: string; headers: Headers }> = [];
  vi.stubGlobal('fetch', (url: string, init: RequestInit) => {
    calls.push({ url, headers: new Headers(init.headers) });
    return Promise.resolve(new Response('{}', { status: 200 }));
  });
  return { calls };
}

describe('backend base resolution', () => {
  it('derives the http base from the websocket URL', () => {
    process.env.OPENPROGRAM_WS = 'ws://127.0.0.1:45231/ws';
    expect(backendBase()).toBe('http://127.0.0.1:45231');
  });

  it('prefers the explicit backend URL', () => {
    withBackend();
    expect(backendBase()).toBe('http://127.0.0.1:45231');
  });
});

describe('isBackendUrl', () => {
  it('accepts the backend origin over http and ws', () => {
    withBackend();
    expect(isBackendUrl('http://127.0.0.1:45231/api/commands')).toBe(true);
    expect(isBackendUrl('ws://127.0.0.1:45231/ws')).toBe(true);
  });

  it('rejects every other host, port, and scheme', () => {
    withBackend();
    for (const url of [
      'http://127.0.0.1:45232/api/commands',
      'https://127.0.0.1:45231/api/commands',
      'http://evil.example.com/api/commands',
      'http://127.0.0.1:45231.evil.com/api/commands',
      'http://user@evil.com/#http://127.0.0.1:45231',
      'file:///etc/passwd',
      'not a url',
    ]) {
      expect(isBackendUrl(url), url).toBe(false);
    }
  });
});

describe('backendFetch', () => {
  it('attaches Bearer + Origin for the backend', async () => {
    withBackend();
    const { calls } = captureFetch();
    await backendFetch('http://127.0.0.1:45231/api/commands');
    expect(calls[0]!.headers.get('Authorization')).toBe(`Bearer ${TOKEN}`);
    expect(calls[0]!.headers.get('Origin')).toBe('http://localhost:45231');
  });

  it('never sends the token to an external URL', async () => {
    withBackend();
    const { calls } = captureFetch();
    for (const url of [
      'http://evil.example.com/collect',
      'https://api.anthropic.com/v1/models',
      'http://127.0.0.1:45232/api/commands',
    ]) {
      await backendFetch(url, {
        headers: { Authorization: `Bearer ${TOKEN}` },
      });
    }
    for (const call of calls) {
      expect(call.headers.get('Authorization'), call.url).toBeNull();
      expect(JSON.stringify([...call.headers])).not.toContain(TOKEN);
    }
  });

  it('refuses to follow a redirect on an authorized request', async () => {
    withBackend();
    const inits: RequestInit[] = [];
    vi.stubGlobal('fetch', (_url: string, init: RequestInit) => {
      inits.push(init);
      return Promise.resolve(new Response('{}', { status: 200 }));
    });
    await backendFetch('http://127.0.0.1:45231/api/commands');
    await backendFetch('http://evil.example.com/collect');
    expect(inits[0]!.redirect).toBe('error');
    expect(inits[1]!.redirect).toBe('follow');
  });

  it('sends nothing when no token is present', async () => {
    process.env.OPENPROGRAM_BACKEND_URL = 'http://127.0.0.1:45231';
    const { calls } = captureFetch();
    await backendFetch('http://127.0.0.1:45231/api/commands');
    expect(calls[0]!.headers.get('Authorization')).toBeNull();
    expect(backendAuthHeaders()).toEqual({});
  });
});

describe('webUiUrls', () => {
  it('keeps the token out of the displayed URL', () => {
    withBackend();
    const urls = webUiUrls()!;
    expect(urls.display).toBe('http://localhost:45231');
    expect(urls.display).not.toContain(TOKEN);
    expect(urls.bootstrap).toBe(`http://localhost:45231/#token=${TOKEN}`);
  });

  it('returns null without a verified endpoint', () => {
    expect(webUiUrls()).toBeNull();
  });
});

/**
 * Real-server round trip.
 *
 * Every test above stubs `fetch`, so they assert which headers we build
 * and never whether the server accepts them. That gap let a 403 ship: the
 * launcher points the base at the loopback IP but sets Origin to the
 * canonical `http://localhost:PORT`, and the owner-auth middleware used
 * to require the two to be the same spelling. These tests run
 * `backendFetch` and the real `ws` client against a live Python listener,
 * with no stubbing, so the header set is judged by the actual server.
 *
 * Skipped when the Python side is unavailable (e.g. a bare `npm test`).
 */
describe('against the real backend listener', () => {
  // fileURLToPath, not .pathname: a repo path containing spaces comes
  // back percent-encoded from .pathname and no longer exists on disk.
  const repoRoot = fileURLToPath(new URL('../../', import.meta.url));
  const python = (() => {
    const r = spawnSync('python3', ['-c', 'import openprogram, uvicorn'], {
      cwd: repoRoot,
    });
    return r.status === 0 ? 'python3' : null;
  })();

  let server: ReturnType<typeof spawn> | null = null;
  let port = 0;
  let token = '';

  beforeAll(async () => {
    if (!python) return;
    server = spawn(python, ['-m', 'tests.support.helpers.owner_auth_listener'], {
      cwd: repoRoot,
      stdio: ['ignore', 'pipe', 'inherit'],
    });
    // The helper prints one JSON line once it is bound and serving.
    const line = await new Promise<string>((resolve, reject) => {
      let buf = '';
      server!.stdout!.on('data', (c) => {
        buf += String(c);
        const nl = buf.indexOf('\n');
        if (nl >= 0) resolve(buf.slice(0, nl));
      });
      server!.on('exit', (code) => reject(new Error(`helper exited ${code}`)));
      setTimeout(() => reject(new Error('helper did not start')), 30000);
    });
    ({ port, token } = JSON.parse(line));
  }, 40000);

  afterAll(() => {
    server?.kill();
  });

  const maybe = python ? it : it.skip;

  maybe('accepts the launcher header set over HTTP and WebSocket', async () => {
    // Exactly what cli/ink.py exports: base on the IP, Origin on localhost.
    process.env.OPENPROGRAM_BACKEND_URL = `http://127.0.0.1:${port}`;
    process.env.OPENPROGRAM_BACKEND_ORIGIN = `http://localhost:${port}`;
    process.env.OPENPROGRAM_BACKEND_TOKEN = token;

    const posted = await backendFetch(
      `http://127.0.0.1:${port}/api/x`,
      { method: 'POST' },
    );
    expect(posted.status).toBe(200);
    expect(await posted.json()).toEqual({ tier: 'owner' });

    const status = await new Promise<number | string>((resolve) => {
      const ws = new WebSocket(`ws://127.0.0.1:${port}/ws`, {
        headers: backendAuthHeaders(),
      });
      let code = 0;
      ws.on('upgrade', (m) => { code = m.statusCode ?? 0; });
      ws.on('open', () => { ws.close(); resolve(code || 101); });
      ws.on('error', (e) => resolve(code || String(e)));
      setTimeout(() => resolve(code || 'timeout'), 15000);
    });
    expect(status).toBe(101);
  }, 40000);

  maybe('is refused when the Origin is not an effective origin', async () => {
    process.env.OPENPROGRAM_BACKEND_URL = `http://127.0.0.1:${port}`;
    process.env.OPENPROGRAM_BACKEND_ORIGIN = 'http://evil.example.com';
    process.env.OPENPROGRAM_BACKEND_TOKEN = token;

    const posted = await backendFetch(
      `http://127.0.0.1:${port}/api/x`,
      { method: 'POST' },
    );
    expect(posted.status).toBe(403);
  }, 40000);
});
