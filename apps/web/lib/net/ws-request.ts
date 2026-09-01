/**
 * One-shot WebSocket request over the shared app socket: send
 * `{action, ...payload}`, resolve with the `data` of the next frame whose
 * `type` matches `responseType`. Resolves null on timeout / no socket.
 *
 * Shared by ProjectMenu, the Projects page, and rule management — anything
 * that does a request/response pair over the one worker WS.
 */
import { getSocket } from "@/lib/runtime-bridge/state";

export interface WsRequestOptions {
  signal?: AbortSignal;
  requestId?: boolean;
}

function requestId(): string {
  const randomUUID = globalThis.crypto?.randomUUID;
  if (randomUUID) return randomUUID.call(globalThis.crypto);
  throw new Error("Web Crypto randomUUID is required");
}

const CORRELATED_ACTIONS = new Set([
  "project_file_tree", "project_file_search", "project_file_read",
  "project_file_write", "project_file_create", "project_file_rename",
  "project_file_copy", "project_file_delete", "project_file_reveal",
  "list_turn_files", "turn_file_diff", "review_scope", "review_file_diff",
  "turn_history_state", "revert_turn", "reapply_turn",
]);

export function wsRequest<T = unknown>(
  action: string,
  payload: Record<string, unknown>,
  responseType: string,
  // 为什么要 match：同一类型的请求可能并发（例如侧栏 Projects 分组、
  // topbar 项目徽标、右栏文件树各发一条 list_projects），仅按 type 匹配
  // 会拿到"别人"那条请求的回复。传 match 后只认谓词通过的帧（通常校验
  // 后端回显的请求参数），其余同类型帧跳过、继续等自己的回复。
  matchOrOptions?: ((data: T) => boolean) | WsRequestOptions,
  timeoutMs = 4000,
  options: WsRequestOptions = {},
): Promise<T | null> {
  const match = typeof matchOrOptions === "function" ? matchOrOptions : undefined;
  const requestOptions = typeof matchOrOptions === "function"
    ? options
    : matchOrOptions ?? options;
  const ws = getSocket();
  return new Promise((resolve) => {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      resolve(null);
      return;
    }
    if (requestOptions.signal?.aborted) {
      resolve(null);
      return;
    }
    const id = (requestOptions.requestId ?? CORRELATED_ACTIONS.has(action))
      ? requestId()
      : null;
    let done = false;
    const finish = (value: T | null) => {
      if (done) return;
      done = true;
      ws.removeEventListener("message", onMsg);
      requestOptions.signal?.removeEventListener("abort", onAbort);
      resolve(value);
    };
    const onMsg = (e: MessageEvent) => {
      try {
        const m = JSON.parse(e.data as string);
        if (
          m && m.type === responseType
          && (!id || m.data?.request_id === id)
          && (!match || match(m.data as T))
        ) {
          finish(m.data as T);
        }
      } catch {
        /* ignore non-JSON frames */
      }
    };
    const onAbort = () => finish(null);
    ws.addEventListener("message", onMsg);
    requestOptions.signal?.addEventListener("abort", onAbort, { once: true });
    ws.send(JSON.stringify({
      action, ...payload, ...(id ? { request_id: id } : {}),
    }));
    setTimeout(() => {
      finish(null);
    }, timeoutMs);
  });
}
