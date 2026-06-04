/**
 * Backend HTTP base + browser opener shared by TUI flows that talk to
 * the worker's REST API (the same FastAPI app the WS rides on).
 *
 * The worker serves WS on `OPENPROGRAM_WS` (e.g. ws://127.0.0.1:18109/ws)
 * and REST on the same host:port. We derive the http base from that so
 * the TUI hits whatever port the user actually launched on — no second
 * config. Falls back to the same default the WS client uses (18109), not
 * the older 8765 some call sites still hard-code.
 */

export function backendBase(): string {
  return (
    process.env.OPENPROGRAM_BACKEND_URL
    || process.env.OPENPROGRAM_WS?.replace('ws://', 'http://').replace('/ws', '')
    || 'http://127.0.0.1:18109'
  );
}

/**
 * Best-effort open a URL in the user's browser. Detached + unref'd so it
 * never blocks the Ink render loop; silently no-ops if the opener isn't
 * available (the caller always also prints the URL as a fallback).
 */
export function openInBrowser(url: string): void {
  void import('child_process').then(({ spawn }) => {
    const opener =
      process.platform === 'darwin' ? 'open'
      : process.platform === 'win32' ? 'start'
      : 'xdg-open';
    try {
      spawn(opener, [url], { stdio: 'ignore', detached: true }).unref();
    } catch {
      /* ignore — URL is printed by the caller as a fallback */
    }
  });
}
