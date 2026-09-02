/**
 * One-shot WebSocket request over the shared app socket: send
 * `{action, ...payload}`, resolve with the `data` of the next frame whose
 * `type` matches `responseType`. Resolves null on timeout / no socket.
 *
 * Shared by ProjectMenu, the Projects page, and rule management — anything
 * that does a request/response pair over the one worker WS.
 */
import { getSocket } from "@/lib/runtime-bridge/state";

const pendingRequestIds = new Map<string, string>();
const mutationKeys = new Map<string, {
  key: string;
  touchedAt: number;
  bytes: number;
  terminal: boolean;
}>();
const pendingMutations = new Map<string, {
  promise: Promise<unknown>;
  operationId?: string;
}>();
const MAX_MUTATION_KEYS = 128;
const MAX_MUTATION_REGISTRY_BYTES = 64 * 1024;
const MAX_MUTATION_ATTEMPTS = 3;
const MAX_MUTATION_VALUE_SAMPLE = 4096;

export class MutationRegistryCapacityError extends Error {
  constructor() {
    super("file mutation registry is full; retry the existing operation");
    this.name = "MutationRegistryCapacityError";
  }
}

function hashText(value: string): string {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

function boundedMutationValue(value: unknown, depth = 0): unknown {
  if (typeof value === "string") {
    if (value.length <= MAX_MUTATION_VALUE_SAMPLE) return value;
    return {
      type: "string",
      length: value.length,
      hash: hashText(value),
      head: value.slice(0, 64),
      tail: value.slice(-64),
    };
  }
  if (depth >= 4) return typeof value;
  if (Array.isArray(value)) {
    const items = value.length <= 64
      ? value
      : [...value.slice(0, 32), ...value.slice(-32)];
    return {
      type: "array",
      length: value.length,
      items: items.map((item) => boundedMutationValue(item, depth + 1)),
    };
  }
  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right));
    const selected = entries.length <= 64
      ? entries
      : [...entries.slice(0, 32), ...entries.slice(-32)];
    return {
      type: "object",
      keys: entries.length,
      values: Object.fromEntries(
        selected
          .map(([key, item]) => [key, boundedMutationValue(item, depth + 1)]),
      ),
    };
  }
  return value;
}

function mutationIdentity(scope: string, payload: Record<string, unknown>): string {
  const summary = JSON.stringify(boundedMutationValue(payload));
  return `${scope.slice(0, 128)}:${summary.length}:${hashText(summary)}`;
}

function registryBytes(identity: string, key: string): number {
  return identity.length + key.length;
}

function currentRegistryBytes(): number {
  let bytes = 0;
  for (const entry of mutationKeys.values()) bytes += entry.bytes;
  return bytes;
}

function pruneMutationKeys(): void {
  const bytes = currentRegistryBytes();
  if (mutationKeys.size < MAX_MUTATION_KEYS && bytes < MAX_MUTATION_REGISTRY_BYTES) return;
  const candidates = [...mutationKeys.entries()]
    .filter(([, entry]) => entry.terminal && !pendingMutations.has(entry.key))
    .sort(([, left], [, right]) => left.touchedAt - right.touchedAt);
  while (
    (mutationKeys.size >= MAX_MUTATION_KEYS
      || currentRegistryBytes() >= MAX_MUTATION_REGISTRY_BYTES)
    && candidates.length
  ) {
    const [identity] = candidates.shift()!;
    mutationKeys.delete(identity);
  }
}

export function mutationRegistryStats(): {
  entries: number;
  bytes: number;
  pending: number;
} {
  return {
    entries: mutationKeys.size,
    bytes: currentRegistryBytes(),
    pending: pendingMutations.size,
  };
}

/** Return the stable idempotency key for one logical UI operation. */
export function idempotencyKeyFor(
  scope: string,
  payload: Record<string, unknown>,
): string {
  const identity = mutationIdentity(scope, payload);
  const existing = mutationKeys.get(identity);
  if (existing) {
    existing.touchedAt = Date.now();
    return existing.key;
  }
  pruneMutationKeys();
  const keyBytes = registryBytes(identity, "00000000-0000-0000-0000-000000000000");
  if (mutationKeys.size >= MAX_MUTATION_KEYS
    || currentRegistryBytes() + keyBytes > MAX_MUTATION_REGISTRY_BYTES) {
    throw new MutationRegistryCapacityError();
  }
  const key = requestId();
  mutationKeys.set(identity, {
    key,
    touchedAt: Date.now(),
    bytes: registryBytes(identity, key),
    terminal: false,
  });
  return key;
}

function forgetMutationKey(key: string): void {
  for (const [identity, entry] of mutationKeys) {
    if (entry.key === key) mutationKeys.delete(identity);
  }
}

export interface WsMutationOptions {
  signal?: AbortSignal;
  maxAttempts?: number;
}

function mutationIsTerminal(value: unknown): boolean {
  if (!value || typeof value !== "object") return false;
  const status = (value as Record<string, unknown>).status;
  return status !== "in_progress" && status !== "pending";
}

/** Retry transient transport/in-progress replies with one idempotency key. */
export function wsMutationRequest<T>(
  key: string,
  send: (signal: AbortSignal) => Promise<T | null>,
  options: WsMutationOptions = {},
): Promise<T | null> {
  const existing = pendingMutations.get(key);
  if (existing) return existing.promise as Promise<T | null>;
  const maxAttempts = Math.max(1, Math.min(
    options.maxAttempts ?? MAX_MUTATION_ATTEMPTS,
    MAX_MUTATION_ATTEMPTS,
  ));
  const run = (async () => {
    let result: T | null = null;
    for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
      if (options.signal?.aborted) break;
      const requestController = new AbortController();
      const onAbort = () => requestController.abort();
      options.signal?.addEventListener("abort", onAbort, { once: true });
      try {
        result = await send(requestController.signal);
      } catch {
        result = null;
      } finally {
        options.signal?.removeEventListener("abort", onAbort);
      }
      const operationId = result && typeof result === "object"
        ? (result as Record<string, unknown>).operation_id
        : undefined;
      if (typeof operationId === "string") {
        const pending = pendingMutations.get(key);
        if (pending) pending.operationId = operationId;
      }
      if (mutationIsTerminal(result) || options.signal?.aborted) break;
    }
    const terminal = mutationIsTerminal(result);
    if (terminal) forgetMutationKey(key);
    else {
      for (const entry of mutationKeys.values()) {
        if (entry.key === key) entry.terminal = false;
      }
    }
    return result;
  })();
  pendingMutations.set(key, { promise: run });
  void run.then(
    () => pendingMutations.delete(key),
    () => pendingMutations.delete(key),
  );
  return run;
}

/** Used by the shared WS dispatcher to avoid toasting a request-local error. */
export function isWsRequestPending(requestIdValue: unknown, action?: unknown): boolean {
  return typeof requestIdValue === "string"
    && pendingRequestIds.get(requestIdValue) === action;
}

/** Register a manually-dispatched request (Review keeps snapshot handling local). */
export function registerWsRequest(requestIdValue: string, action: string): () => void {
  pendingRequestIds.set(requestIdValue, action);
  return () => pendingRequestIds.delete(requestIdValue);
}

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
    if (id) pendingRequestIds.set(id, action);
    let done = false;
    let timeout: ReturnType<typeof setTimeout> | undefined;
    const finish = (value: T | null) => {
      if (done) return;
      done = true;
      if (timeout !== undefined) clearTimeout(timeout);
      if (id) pendingRequestIds.delete(id);
      ws.removeEventListener("message", onMsg);
      ws.removeEventListener("close", onClose);
      requestOptions.signal?.removeEventListener("abort", onAbort);
      resolve(value);
    };
    const onMsg = (e: MessageEvent) => {
      try {
        const m = JSON.parse(e.data as string);
        if (m?.type === "operation_error" && id
          && m.data?.request_id === id
          && m.data?.action === action) {
          const errorData = {
            ...(m.data as Record<string, unknown>),
            status: "error",
            error_code: m.data?.code,
            error: m.data?.message,
          } as T;
          finish(errorData);
          return;
        }
        if (
          m && m.type === responseType
          && (!id || m.data?.request_id === id)
          && (!id || m.data?.action === action)
          && (!match || match(m.data as T))
        ) {
          finish(m.data as T);
        }
      } catch {
        /* ignore non-JSON frames */
      }
    };
    const onClose = () => finish(null);
    const onAbort = () => finish(null);
    ws.addEventListener("message", onMsg);
    ws.addEventListener("close", onClose);
    requestOptions.signal?.addEventListener("abort", onAbort, { once: true });
    try {
      ws.send(JSON.stringify({
        action, ...payload, ...(id ? { request_id: id } : {}),
      }));
    } catch {
      finish(null);
      return;
    }
    timeout = setTimeout(() => {
      finish(null);
    }, timeoutMs);
  });
}
