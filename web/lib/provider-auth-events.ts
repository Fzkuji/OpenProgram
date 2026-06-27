/**
 * Subscribe to the /api/providers/events SSE stream.
 *
 * Returns an unsubscribe function. Automatically reconnects with
 * exponential backoff on network errors; individual event parsing
 * failures are swallowed (one bad frame shouldn't break the stream).
 *
 * Callers use this from a React effect:
 *
 *   useEffect(() => subscribeProviderAuthEvents(e => toast(e.type)), []);
 */
import type { AuthEventPayload } from "./types";

type Handler = (event: AuthEventPayload) => void;

const INITIAL_BACKOFF_MS = 500;
const MAX_BACKOFF_MS = 30_000;

export function subscribeProviderAuthEvents(onEvent: Handler): () => void {
  let source: EventSource | null = null;
  let closed = false;
  let backoff = INITIAL_BACKOFF_MS;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  const connect = () => {
    if (closed) return;
    source = new EventSource("/api/providers/events");
    source.onmessage = (msg) => {
      // A successful message resets the backoff — sustained connections
      // that hiccup once shouldn't accumulate penalty.
      backoff = INITIAL_BACKOFF_MS;
      try {
        const payload = JSON.parse(msg.data) as AuthEventPayload;
        onEvent(payload);
      } catch {
        // Malformed frame — skip. The SSE keepalive comments don't land
        // here (EventSource filters them), so this only fires on
        // genuine parser failures.
      }
    };
    source.onerror = () => {
      // EventSource triggers onerror for both transient and terminal
      // failures. We close and schedule a retry — browsers' built-in
      // retry policy is too aggressive and can spam logs on a dead server.
      source?.close();
      source = null;
      if (closed) return;
      reconnectTimer = setTimeout(connect, backoff);
      backoff = Math.min(backoff * 2, MAX_BACKOFF_MS);
    };
  };

  connect();

  return () => {
    closed = true;
    if (reconnectTimer) clearTimeout(reconnectTimer);
    source?.close();
  };
}
