/**
 * Backend HTTP base + browser opener shared by TUI flows that talk to
 * the worker's REST API (the same FastAPI app the WS rides on).
 *
 * The worker serves WS on `OPENPROGRAM_WS` (e.g. ws://127.0.0.1:18100/ws)
 * and REST on the same host:port. We derive the http base from that so
 * the TUI hits whatever port the user actually launched on — no second
 * config. Falls back to the same default the WS client uses (18100, the
 * single port since the frontend moved onto the backend — see
 * docs/reference/design/cli/single-port.html).
 */
import { spawn } from 'child_process';

export function backendBase(): string {
  return (
    process.env.OPENPROGRAM_BACKEND_URL
    || process.env.OPENPROGRAM_WS
      ?.replace('wss://', 'https://')
      .replace('ws://', 'http://')
      .replace(/\/ws$/, '')
    || 'http://127.0.0.1:18100'
  );
}

/**
 * Owner token for the backend, handed down by the Python launcher through
 * the environment (openprogram/interfaces/cli/ink.py). Empty when
 * the TUI was started
 * without a verified backend endpoint.
 */
export function backendToken(): string {
  return process.env.OPENPROGRAM_BACKEND_TOKEN ?? '';
}

/** Authorization + Origin headers for one backend request, or {} when
 *  there is no token to send. */
export function backendAuthHeaders(): Record<string, string> {
  const token = backendToken();
  if (!token) return {};
  const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
  const origin = process.env.OPENPROGRAM_BACKEND_ORIGIN;
  if (origin) headers.Origin = origin;
  return headers;
}

/** True only when `url` addresses the configured backend itself — same
 *  origin AND under the backend base path. Anything else (a provider API,
 *  a docs link, an attacker-supplied URL) must never see the token. */
export function isBackendUrl(url: string): boolean {
  let target: URL;
  let base: URL;
  try {
    base = new URL(backendBase());
    // Absolute only. A relative URL would resolve against the backend and
    // so always "match", which would make the check useless as a guard.
    target = new URL(
      url.replace(/^ws:\/\//, 'http://').replace(/^wss:\/\//, 'https://'),
    );
  } catch {
    return false;
  }
  if (target.origin !== base.origin) return false;
  const prefix = base.pathname.replace(/\/$/, '');
  return target.pathname === prefix || target.pathname.startsWith(`${prefix}/`);
}

/**
 * `fetch` for backend calls. Attaches the owner token ONLY when the
 * resolved URL is the backend itself; for any other host the request goes
 * out untouched, so a redirect or a mistyped base can never leak the
 * credential.
 */
export async function backendFetch(
  url: string,
  init: RequestInit = {},
): Promise<Response> {
  const authorized = isBackendUrl(url);
  const headers = new Headers(init.headers);
  if (authorized) {
    for (const [name, value] of Object.entries(backendAuthHeaders())) {
      headers.set(name, value);
    }
  } else {
    headers.delete('Authorization');
  }
  return fetch(url, {
    ...init,
    headers,
    // A redirect off the backend would replay the Authorization header at
    // the new host; refuse to follow instead.
    redirect: authorized ? 'error' : (init.redirect ?? 'follow'),
  });
}

/**
 * Browser URL for the web UI, and the fragment-bootstrap URL that carries
 * the owner token past the login wall. Mirrors
 * `webui.owner_auth.build_owner_auth_url`: the token goes in the fragment,
 * which browsers never send to a server, and only ever onto the effective
 * Origin the launcher verified. `display` stays token-free so it can be
 * printed; `bootstrap` is only ever handed to the browser opener.
 */
export function webUiUrls(): { display: string; bootstrap: string } | null {
  const origin = process.env.OPENPROGRAM_BACKEND_ORIGIN;
  const token = backendToken();
  if (!origin) return null;
  if (!token) return { display: origin, bootstrap: origin };
  return { display: origin, bootstrap: `${origin}/#token=${token}` };
}

/**
 * Best-effort open a URL in the user's browser. Detached + unref'd so it
 * never blocks the Ink render loop; silently no-ops if the opener isn't
 * available (the caller always also prints the URL as a fallback).
 */
export function openInBrowser(
  url: string,
  launch: BrowserLauncher = spawn,
  platform: NodeJS.Platform = process.platform,
): void {
  const [opener, args] = browserOpenCommand(url, platform);
  try {
    const child = launch(opener, [...args], {
      stdio: 'ignore',
      detached: true,
      windowsHide: true,
    });
    // A missing opener is reported as an 'error' event after spawn returns;
    // with no listener Node raises it and the TUI exits.
    child.on('error', () => { /* URL is printed by the caller as a fallback */ });
    child.unref();
  } catch {
    /* ignore — URL is printed by the caller as a fallback */
  }
}

type BrowserLauncher = (
  command: string,
  args: string[],
  options: { stdio: 'ignore'; detached: boolean; windowsHide: boolean },
) => { on(event: 'error', listener: (error: Error) => void): unknown; unref(): void };

/**
 * The program that hands a URL to the user's default browser. `start` is a
 * cmd.exe builtin, not a program Node can spawn, and going through cmd.exe
 * would let `&` in a URL end the command; the URL handler takes it as one
 * argument.
 */
export function browserOpenCommand(
  url: string,
  platform: NodeJS.Platform = process.platform,
): readonly [command: string, args: readonly string[]] {
  if (platform === 'darwin') return ['open', [url]];
  if (platform === 'win32') return ['rundll32', ['url.dll,FileProtocolHandler', url]];
  return ['xdg-open', [url]];
}
