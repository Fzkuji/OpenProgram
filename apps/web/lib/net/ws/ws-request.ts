/**
 * One-shot WebSocket request over the shared app socket: send
 * `{action, ...payload}`, resolve with the `data` of the next frame whose
 * `type` matches `responseType`. Resolves null on timeout / no socket.
 *
 * Shared by ProjectMenu, the Projects page, and rule management — anything
 * that does a request/response pair over the one worker WS.
 */
import { getSocket } from "@/lib/runtime-bridge/state";
import { onChanged } from "@/lib/net/ws/change-bus";

/** The Worker's `changed` topic for durable write receipts: its keys are
 *  the idempotency keys whose receipt was written or became final
 *  (`openprogram/state/store/operation_receipts.py`). */
export const OPERATION_RECEIPTS = "operation.receipts";

const pendingRequestIds = new Map<string, string>();
const mutationKeys = new Map<string, {
  key: string;
  action: string;
  projectId: string | undefined;
  targetId: string | undefined;
  touchedAt: number;
  bytes: number;
  terminal: boolean;
  operationId?: string;
}>();
const pendingMutations = new Map<string, {
  promise: Promise<unknown>;
  operationId?: string;
}>();
const MAX_MUTATION_KEYS = 128;
const MAX_MUTATION_REGISTRY_BYTES = 64 * 1024;
const MAX_MUTATION_ATTEMPTS = 3;
const MAX_MUTATION_VALUE_SAMPLE = 4096;
const mutationReconciliations = new Map<string, {
  promise: Promise<void>;
  controller: AbortController;
  resetGeneration: number;
  keyGeneration: number;
}>();
const mutationReconcileKeyGenerations = new Map<string, number>();
let mutationReconcileResetGeneration = 0;

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

function reconcileMutationRegistryBeforeRejecting(): void {
  for (const entry of mutationKeys.values()) {
    if (!entry.terminal) reconcileWsMutation(entry.key);
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
    reconcileMutationRegistryBeforeRejecting();
    throw new MutationRegistryCapacityError();
  }
  const key = requestId();
  mutationKeys.set(identity, {
    key,
    action: scope,
    projectId: typeof payload.project_id === "string"
      ? payload.project_id
      : typeof payload.session_id === "string" ? payload.session_id : undefined,
    targetId: typeof payload.msg_id === "string"
      ? payload.msg_id
      : typeof payload.assistant_msg_id === "string" ? payload.assistant_msg_id : undefined,
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
  deadlineMs?: number;
  reconcile?: boolean;
}

function mutationIsTerminal(value: unknown): boolean {
  if (!value || typeof value !== "object") return false;
  const status = (value as Record<string, unknown>).status;
  return status !== "in_progress" && status !== "pending";
}

function subscribeMutation<T>(shared: Promise<T | null>, signal?: AbortSignal): Promise<T | null> {
  if (!signal) return shared;
  if (signal.aborted) return Promise.resolve(null);
  return new Promise((resolve) => {
    let detached = false;
    const onAbort = () => {
      if (detached) return;
      detached = true;
      signal.removeEventListener("abort", onAbort);
      resolve(null);
    };
    signal.addEventListener("abort", onAbort, { once: true });
    shared.then(
      (value) => {
        if (detached) return;
        detached = true;
        signal.removeEventListener("abort", onAbort);
        resolve(value);
      },
      () => {
        if (detached) return;
        detached = true;
        signal.removeEventListener("abort", onAbort);
        resolve(null);
      },
    );
  });
}

/** Retry transient transport/in-progress replies with one idempotency key.
 * `signal` belongs to the caller subscription; it never cancels this shared
 * durable operation, because another view may still be consuming the same
 * receipt. */
export function wsMutationRequest<T>(
  key: string,
  send: (signal: AbortSignal) => Promise<T | null>,
  options: WsMutationOptions = {},
): Promise<T | null> {
  if (options.signal?.aborted) return Promise.resolve(null);
  const existing = pendingMutations.get(key);
  if (existing) return subscribeMutation(existing.promise as Promise<T | null>, options.signal);
  const maxAttempts = Math.max(1, Math.min(
    options.maxAttempts ?? MAX_MUTATION_ATTEMPTS,
    MAX_MUTATION_ATTEMPTS,
  ));
  const deadlineMs = Math.max(0, options.deadlineMs ?? 4000);
  const run = (async () => {
    let result: T | null = null;
    let attempt = 0;
    // Heard from the first send on, so a receipt that lands while the
    // reply is on its way still counts.
    const receipt = receiptWaiter(key);
    try {
      while (attempt < maxAttempts) {
        attempt += 1;
        const requestController = new AbortController();
        try {
          result = await send(requestController.signal);
        } catch {
          result = null;
        }
        const operationId = result && typeof result === "object"
          ? (result as Record<string, unknown>).operation_id
          : undefined;
        if (typeof operationId === "string") {
          const pending = pendingMutations.get(key);
          if (pending) pending.operationId = operationId;
          for (const entry of mutationKeys.values()) {
            if (entry.key === key) entry.operationId = operationId;
          }
        }
        if (mutationIsTerminal(result)) break;
        const status = result && typeof result === "object"
          ? (result as Record<string, unknown>).status
          : undefined;
        if (status === "in_progress" || status === "pending") {
          // Another request holds the operation: ask again when the Worker
          // says its receipt changed (or the socket came back), until the
          // deadline.
          const deadline = Date.now() + deadlineMs;
          while (Date.now() < deadline) {
            await receipt.next(undefined, deadline - Date.now());
            const pollController = new AbortController();
            try {
              const next = await send(pollController.signal);
              // A lost poll response is transport uncertainty, not evidence
              // that the durable operation disappeared. Keep the last known
              // in-progress receipt so the deadline still yields recovery_required.
              if (next !== null) result = next;
            } catch {
              // Preserve the last known receipt until the deadline.
            }
            const nextOperationId = result && typeof result === "object"
              ? (result as Record<string, unknown>).operation_id
              : undefined;
            if (typeof nextOperationId === "string") {
              const pending = pendingMutations.get(key);
              if (pending) pending.operationId = nextOperationId;
              for (const entry of mutationKeys.values()) {
                if (entry.key === key) entry.operationId = nextOperationId;
              }
            }
            if (mutationIsTerminal(result)) break;
          }
          break;
        }
      }
    } finally {
      receipt.stop();
    }
    if (result && typeof result === "object") {
      const status = (result as Record<string, unknown>).status;
      if ((status === "in_progress" || status === "pending")
      ) {
        result = {
          ...(result as Record<string, unknown>),
          status: "recovery_required",
          error_code: "RECOVERY_REQUIRED",
          error: "The file operation did not reach a terminal receipt before the deadline.",
        } as T;
      }
    }
    const terminal = mutationIsTerminal(result);
    const unresolved = result && typeof result === "object"
      && ["in_progress", "pending", "recovery_required"].includes(
        (result as Record<string, unknown>).status as string,
      );
    if (terminal && !unresolved) forgetMutationKey(key);
    else {
      for (const entry of mutationKeys.values()) {
        if (entry.key === key) entry.terminal = false;
      }
    }
    return result;
  })();
  pendingMutations.set(key, { promise: run });
  void run.then(
    (value) => {
      if (pendingMutations.get(key)?.promise === run) pendingMutations.delete(key);
      if (options.reconcile !== false) {
        // The caller may have detached, but the server receipt still needs
        // reconciliation. Never resend a write payload from this path.
        const status = value && typeof value === "object"
          ? (value as Record<string, unknown>).status : undefined;
        if (!value || ["in_progress", "pending", "recovery_required"].includes(status as string))
          reconcileWsMutation(key);
      }
    },
    () => {
      if (pendingMutations.get(key)?.promise === run) pendingMutations.delete(key);
      if (options.reconcile !== false) reconcileWsMutation(key);
    },
  );
  return subscribeMutation(run, options.signal);
}

function mutationEntryForKey(key: string) {
  for (const entry of mutationKeys.values()) {
    if (entry.key === key) return entry;
  }
  return undefined;
}

function mutationStatusRequest(entry: {
  action: string;
  projectId: string | undefined;
  targetId: string | undefined;
}, key: string): {
  action: string;
  responseType: string;
  payload: Record<string, unknown>;
  matches: (data: Record<string, unknown>) => boolean;
} | null {
  if (entry.action.startsWith("project_file_")) {
    return {
      action: "project_file_operation_status",
      responseType: "project_file_operation_status_result",
      payload: {
        project_id: entry.projectId ?? "",
        operation_action: entry.action,
        idempotency_key: key,
      },
      matches: (data) => data.project_id === entry.projectId
        && data.operation_action === entry.action
        && data.idempotency_key === key,
    };
  }
  const turnAction = /^(revert_turn|reapply_turn):/.exec(entry.action)?.[1];
  if (!turnAction || !entry.targetId) return null;
  return {
    action: "turn_operation_status",
    responseType: "turn_operation_status_result",
    payload: {
      session_id: entry.projectId ?? "",
      msg_id: entry.targetId,
      operation_action: turnAction,
      idempotency_key: key,
    },
    matches: (data) => data.session_id === entry.projectId
      && data.operation_action === turnAction
      && data.msg_id === entry.targetId
      && data.idempotency_key === key,
  };
}

/** Wake on the Worker's word that the receipt of `key` changed, or on a
 *  reconnect (frames sent while the socket was down are lost). A word heard
 *  while nobody waited is kept for the next wait. */
function receiptWaiter(key: string): {
  next: (signal?: AbortSignal, deadlineMs?: number) => Promise<void>;
  stop: () => void;
} {
  let heard = false;
  let wake: (() => void) | null = null;
  const off = onChanged(OPERATION_RECEIPTS, (keys) => {
    if (keys !== null && !keys.includes(key)) return;
    heard = true;
    wake?.();
  });
  return {
    next(signal, deadlineMs) {
      if (heard || signal?.aborted || deadlineMs !== undefined && deadlineMs <= 0) {
        heard = false;
        return Promise.resolve();
      }
      return new Promise((resolve) => {
        let timer: ReturnType<typeof setTimeout> | undefined;
        const done = () => {
          wake = null;
          heard = false;
          if (timer !== undefined) clearTimeout(timer);
          signal?.removeEventListener("abort", done);
          resolve();
        };
        wake = done;
        signal?.addEventListener("abort", done, { once: true });
        if (deadlineMs !== undefined) {
          timer = setTimeout(done, deadlineMs);
          unrefTimer(timer);
        }
      });
    },
    stop: () => { off(); wake?.(); },
  };
}

// Browser timers are numeric handles; Node timers expose `unref`. Keeping
// request deadlines unreferenced prevents a test/SSR process from being
// held open by a best-effort background request while preserving browser use.
function unrefTimer(timer: ReturnType<typeof setTimeout>): void {
  const nodeTimer = timer as unknown as { unref?: () => void };
  nodeTimer.unref?.();
}

/** Reconcile an aborted UI request without replaying a write payload. The
 * durable server receipt is asked for once now, then once each time the
 * Worker says that receipt changed and once after each reconnect, until an
 * identified terminal receipt answers; nothing asks on a timer. */
export function reconcileWsMutation(key: string): void {
  const resetGeneration = mutationReconcileResetGeneration;
  const keyGeneration = mutationReconcileKeyGenerations.get(key) ?? 0;
  // The shared durable request still owns the receipt; defer reconciliation
  // until that promise settles so status asks never race its request.
  const pending = pendingMutations.get(key);
  if (pending) {
    void pending.promise.then(() => {
      if (resetGeneration === mutationReconcileResetGeneration
        && keyGeneration === (mutationReconcileKeyGenerations.get(key) ?? 0)) {
        reconcileWsMutation(key);
      }
    });
    return;
  }
  if (mutationReconciliations.has(key)) return;
  const controller = new AbortController();
  const receipt = receiptWaiter(key);
  const run = (async () => {
    while (!controller.signal.aborted
      && resetGeneration === mutationReconcileResetGeneration
      && keyGeneration === (mutationReconcileKeyGenerations.get(key) ?? 0)) {
      const entry = mutationEntryForKey(key);
      if (!entry) return;
      if (pendingMutations.has(key)) return;
      const statusRequest = mutationStatusRequest(entry, key);
      if (!statusRequest) return;
      const statusPayload = {
        ...statusRequest.payload,
        ...(entry.operationId ? { operation_id: entry.operationId } : {}),
      };
      let result: Record<string, unknown> | null = null;
      try {
        result = await wsRequest<Record<string, unknown>>(
          statusRequest.action,
          statusPayload,
          statusRequest.responseType,
          statusRequest.matches,
          4000,
          { requestId: true, signal: controller.signal },
        );
      } catch {
        // Request setup/handler failures are uncertainty, not evidence that
        // the durable operation is gone: keep the key and ask again on the
        // next receipt word or reconnect.
      }
      const status = result?.status;
      const expectedOperationId = entry.operationId;
      const returnedOperationId = typeof result?.operation_id === "string"
        ? result.operation_id : undefined;
      if (returnedOperationId
        && (!expectedOperationId || expectedOperationId === returnedOperationId)) {
        entry.operationId = returnedOperationId;
      }
      const identifiedRecovery = status === "recovery_required"
        && result !== null
        && statusRequest.matches(result)
        && typeof returnedOperationId === "string"
        && typeof expectedOperationId === "string"
        && expectedOperationId === returnedOperationId;
      const identifiedTerminal = typeof returnedOperationId === "string"
        && (!expectedOperationId || expectedOperationId === returnedOperationId)
        && (status !== "error" || result?.durable_receipt === true);
      if (typeof status === "string" && !["in_progress", "pending"].includes(status)
        && (identifiedRecovery || identifiedTerminal)) {
        forgetMutationKey(key);
        return;
      }
      await receipt.next(controller.signal);
    }
  })();
  const state = {
    promise: run, controller, resetGeneration, keyGeneration,
  };
  mutationReconciliations.set(key, state);
  const settle = () => {
    receipt.stop();
    if (mutationReconciliations.get(key) === state) mutationReconciliations.delete(key);
  };
  void run.then(settle, settle);
}

/** Stop one registry-owned reconciler without releasing its durable key. */
export function stopWsMutationReconciliation(key: string): void {
  mutationReconcileKeyGenerations.set(
    key, (mutationReconcileKeyGenerations.get(key) ?? 0) + 1,
  );
  mutationReconciliations.get(key)?.controller.abort();
}

/** Test/process teardown hook: stop background reconcilers, retain keys. */
export function resetWsMutationReconciliation(): void {
  mutationReconcileResetGeneration += 1;
  for (const key of [...mutationReconciliations.keys()]) stopWsMutationReconciliation(key);
}

/** Used by the shared WS dispatcher to avoid toasting a request-local error. */
export function isWsRequestPending(requestIdValue: unknown, action?: unknown): boolean {
  return typeof requestIdValue === "string"
    && pendingRequestIds.get(requestIdValue) === action;
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
    "project_file_info", "project_folder_size",
  "project_file_operation_status",
  "turn_operation_status",
  "project_file_write", "project_file_create", "project_file_rename",
  "project_file_copy", "project_file_delete", "project_file_reveal",
  "review_scope", "review_file_diff",
  "turn_history_state", "revert_turn", "reapply_turn",
]);

type WsFrame = { type?: unknown; data?: Record<string, unknown> };
type FrameHandler = (frame: WsFrame) => void;

interface SocketDispatch {
  byRequestId: Map<string, FrameHandler>;
  byType: Map<string, Set<FrameHandler>>;
  onClose: Set<() => void>;
  detach: () => void;
}

// One `message` listener per socket serves every pending request and
// wsListen subscriber: each frame is JSON-parsed once and routed by
// `request_id` (correlated replies) or by `type` (uncorrelated replies,
// listeners). It detaches once nothing waits, so an idle socket carries none.
const dispatchers = new WeakMap<WebSocket, SocketDispatch>();

function dispatcherFor(ws: WebSocket): SocketDispatch {
  const existing = dispatchers.get(ws);
  if (existing) return existing;
  const dispatch: SocketDispatch = {
    byRequestId: new Map(),
    byType: new Map(),
    onClose: new Set(),
    detach: () => {},
  };
  const onMessage = (event: MessageEvent) => {
    let frame: WsFrame | null;
    try {
      frame = JSON.parse(String(event.data));
    } catch {
      return; /* ignore non-JSON frames */
    }
    if (!frame || typeof frame !== "object") return;
    const replyId = frame.data?.request_id;
    const routed: FrameHandler[] = [];
    if (typeof replyId === "string") {
      const handler = dispatch.byRequestId.get(replyId);
      if (handler) routed.push(handler);
    }
    if (typeof frame.type === "string") routed.push(...(dispatch.byType.get(frame.type) ?? []));
    // A throwing handler must not starve the others of the frame.
    for (const handler of routed) {
      try { handler(frame); } catch { /* handler error stays local */ }
    }
  };
  const onClose = () => {
    for (const handler of [...dispatch.onClose]) handler();
  };
  ws.addEventListener("message", onMessage);
  ws.addEventListener("close", onClose);
  dispatch.detach = () => {
    ws.removeEventListener("message", onMessage);
    ws.removeEventListener("close", onClose);
    dispatchers.delete(ws);
  };
  dispatchers.set(ws, dispatch);
  return dispatch;
}

function releaseDispatcher(ws: WebSocket, dispatch: SocketDispatch): void {
  if (dispatch.byRequestId.size || dispatch.byType.size || dispatch.onClose.size) return;
  if (dispatchers.get(ws) === dispatch) dispatch.detach();
}

function addTypeHandler(dispatch: SocketDispatch, type: string, handler: FrameHandler): void {
  const handlers = dispatch.byType.get(type) ?? new Set<FrameHandler>();
  handlers.add(handler);
  dispatch.byType.set(type, handlers);
}

function removeTypeHandler(dispatch: SocketDispatch, type: string, handler: FrameHandler): void {
  const handlers = dispatch.byType.get(type);
  if (!handlers) return;
  handlers.delete(handler);
  if (!handlers.size) dispatch.byType.delete(type);
}

/** Listeners currently attached by the shared dispatcher (tests only). */
export function wsDispatcherAttached(ws: WebSocket): boolean {
  return dispatchers.has(ws);
}

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
    const dispatch = dispatcherFor(ws);
    let done = false;
    let timeout: ReturnType<typeof setTimeout> | undefined;
    const finish = (value: T | null) => {
      if (done) return;
      done = true;
      if (timeout !== undefined) clearTimeout(timeout);
      if (id) {
        pendingRequestIds.delete(id);
        if (dispatch.byRequestId.get(id) === onFrame) dispatch.byRequestId.delete(id);
      } else {
        removeTypeHandler(dispatch, responseType, onFrame);
      }
      dispatch.onClose.delete(onClose);
      releaseDispatcher(ws, dispatch);
      requestOptions.signal?.removeEventListener("abort", onAbort);
      resolve(value);
    };
    // Correlated requests only ever see frames echoing their own request_id.
    const onFrame: FrameHandler = (m) => {
      if (id && m.type === "operation_error" && m.data?.action === action) {
        finish({
          ...m.data,
          status: "error",
          error_code: m.data?.code,
          error: m.data?.message,
        } as T);
        return;
      }
      if (
        m.type === responseType
        && (!id || m.data?.action === action)
        && (!match || match(m.data as T))
      ) {
        finish(m.data as T);
      }
    };
    const onClose = () => finish(null);
    const onAbort = () => finish(null);
    if (id) dispatch.byRequestId.set(id, onFrame);
    else addTypeHandler(dispatch, responseType, onFrame);
    dispatch.onClose.add(onClose);
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
    unrefTimer(timeout);
  });
}

/** Hear every frame of one type on the shared socket (progress events and the like). */
export function wsListen<T = unknown>(type: string, handler: (data: T) => void): () => void {
  const ws = getSocket();
  if (!ws) return () => {};
  const dispatch = dispatcherFor(ws);
  const onFrame: FrameHandler = (frame) => handler(frame.data as T);
  addTypeHandler(dispatch, type, onFrame);
  return () => {
    removeTypeHandler(dispatch, type, onFrame);
    releaseDispatcher(ws, dispatch);
  };
}

/** Send an action that has no reply. False when the socket is not open. */
export function wsFire(action: string, payload: Record<string, unknown>): boolean {
  const ws = getSocket();
  if (!ws || ws.readyState !== WebSocket.OPEN) return false;
  try {
    ws.send(JSON.stringify({ action, ...payload }));
    return true;
  } catch {
    return false;
  }
}
