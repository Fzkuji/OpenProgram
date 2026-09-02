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
const MUTATION_RECONCILE_INTERVAL_MS = 250;
const MUTATION_RECONCILE_RETRY_MS = 5000;
const MUTATION_RECONCILE_DEADLINE_MS = 30000;
const mutationReconciliations = new Map<string, {
  promise: Promise<void>;
  controller: AbortController;
  resetGeneration: number;
  keyGeneration: number;
}>();
const mutationReconcileRetryTimers = new Map<string, ReturnType<typeof setTimeout>>();
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
        const deadline = Date.now() + deadlineMs;
        while (Date.now() < deadline) {
          await waitForMutationReconcile(Math.min(100, deadline - Date.now()));
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

function waitForMutationReconcile(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    let done = false;
    let timer: ReturnType<typeof setTimeout>;
    const onAbort = () => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
      resolve();
    };
    timer = setTimeout(onAbort, ms);
    unrefTimer(timer);
    signal?.addEventListener("abort", onAbort, { once: true });
    if (signal?.aborted) onAbort();
  });
}

// Browser timers are numeric handles; Node timers expose `unref`. Keeping
// reconciliation timers unreferenced prevents a test/SSR process from being
// held open by a best-effort background retry while preserving browser use.
function unrefTimer(timer: ReturnType<typeof setTimeout>): void {
  const nodeTimer = timer as unknown as { unref?: () => void };
  nodeTimer.unref?.();
}

/** Reconcile an aborted UI request without replaying a write payload. The
 * durable server receipt is queried with fresh request ids at a bounded rate;
 * connection loss simply causes the next scheduled round to try again. */
export function reconcileWsMutation(key: string): void {
  const retryTimer = mutationReconcileRetryTimers.get(key);
  if (retryTimer !== undefined) clearTimeout(retryTimer);
  mutationReconcileRetryTimers.delete(key);
  const resetGeneration = mutationReconcileResetGeneration;
  const keyGeneration = mutationReconcileKeyGenerations.get(key) ?? 0;
  // The shared durable request still owns the receipt; defer reconciliation
  // until that promise settles so status polling never races its request.
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
  const run = (async () => {
    const deadline = Date.now() + MUTATION_RECONCILE_DEADLINE_MS;
    while (!controller.signal.aborted
      && resetGeneration === mutationReconcileResetGeneration
      && keyGeneration === (mutationReconcileKeyGenerations.get(key) ?? 0)
      && Date.now() < deadline) {
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
          Math.min(4000, Math.max(1, deadline - Date.now())),
          { requestId: true, signal: controller.signal },
        );
      } catch {
        // Request setup/handler failures are uncertainty, not evidence that
        // the durable operation is gone. Keep the key and retry this bounded
        // round; the outer retry timer handles later reconnects.
        await waitForMutationReconcile(MUTATION_RECONCILE_INTERVAL_MS, controller.signal);
        continue;
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
      await waitForMutationReconcile(MUTATION_RECONCILE_INTERVAL_MS, controller.signal);
    }
    if (controller.signal.aborted) return;
    // Keep the key for safe future replay, but start a later bounded round so
    // reconnects can observe a server operation that outlives this deadline.
    const retryTimer = setTimeout(() => {
      mutationReconcileRetryTimers.delete(key);
      if (resetGeneration === mutationReconcileResetGeneration
        && keyGeneration === (mutationReconcileKeyGenerations.get(key) ?? 0)) {
        reconcileWsMutation(key);
      }
    }, MUTATION_RECONCILE_RETRY_MS);
    unrefTimer(retryTimer);
    mutationReconcileRetryTimers.set(key, retryTimer);
  })();
  const state = {
    promise: run, controller, resetGeneration, keyGeneration,
  };
  mutationReconciliations.set(key, state);
  void run.then(
    () => {
      if (mutationReconciliations.get(key) === state) mutationReconciliations.delete(key);
    },
    () => {
      if (mutationReconciliations.get(key) === state) mutationReconciliations.delete(key);
    },
  );
}

/** Stop one registry-owned reconciler without releasing its durable key. */
export function stopWsMutationReconciliation(key: string): void {
  mutationReconcileKeyGenerations.set(
    key, (mutationReconcileKeyGenerations.get(key) ?? 0) + 1,
  );
  mutationReconciliations.get(key)?.controller.abort();
  const timer = mutationReconcileRetryTimers.get(key);
  if (timer !== undefined) clearTimeout(timer);
  mutationReconcileRetryTimers.delete(key);
}

/** Test/process teardown hook: stop background status polling, retain keys. */
export function resetWsMutationReconciliation(): void {
  mutationReconcileResetGeneration += 1;
  for (const key of new Set([
    ...mutationReconciliations.keys(),
    ...mutationReconcileRetryTimers.keys(),
  ])) stopWsMutationReconciliation(key);
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
  "project_file_operation_status",
  "turn_operation_status",
  "project_file_write", "project_file_create", "project_file_rename",
  "project_file_copy", "project_file_delete", "project_file_reveal",
  "review_scope", "review_file_diff",
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
    unrefTimer(timeout);
  });
}
