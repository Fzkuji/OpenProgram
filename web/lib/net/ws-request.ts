/**
 * One-shot WebSocket request over the global `window.ws` socket: send
 * `{action, ...payload}`, resolve with the `data` of the next frame whose
 * `type` matches `responseType`. Resolves null on timeout / no socket.
 *
 * Shared by ProjectMenu, the Projects page, and rule management — anything
 * that does a request/response pair over the one worker WS.
 */
interface WsWindow {
  ws?: WebSocket;
}

export function wsRequest<T = unknown>(
  action: string,
  payload: Record<string, unknown>,
  responseType: string,
  timeoutMs = 4000,
): Promise<T | null> {
  const ws = (window as unknown as WsWindow).ws;
  return new Promise((resolve) => {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      resolve(null);
      return;
    }
    let done = false;
    const onMsg = (e: MessageEvent) => {
      try {
        const m = JSON.parse(e.data as string);
        if (m && m.type === responseType) {
          done = true;
          ws.removeEventListener("message", onMsg);
          resolve(m.data as T);
        }
      } catch {
        /* ignore non-JSON frames */
      }
    };
    ws.addEventListener("message", onMsg);
    ws.send(JSON.stringify({ action, ...payload }));
    setTimeout(() => {
      if (!done) {
        ws.removeEventListener("message", onMsg);
        resolve(null);
      }
    }, timeoutMs);
  });
}
