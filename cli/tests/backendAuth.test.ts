import { afterEach, describe, expect, it, vi } from 'vitest';

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
