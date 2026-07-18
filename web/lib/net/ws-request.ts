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
  // 为什么要 match：同一类型的请求可能并发（例如侧栏 Projects 分组、
  // topbar 项目徽标、右栏文件树各发一条 list_projects），仅按 type 匹配
  // 会拿到"别人"那条请求的回复。传 match 后只认谓词通过的帧（通常校验
  // 后端回显的请求参数），其余同类型帧跳过、继续等自己的回复。
  match?: (data: T) => boolean,
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
        if (m && m.type === responseType && (!match || match(m.data as T))) {
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
