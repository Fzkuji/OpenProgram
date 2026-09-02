import assert from "node:assert/strict";
import test, { after } from "node:test";
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import { fileURLToPath } from "node:url";

const WEB_ROOT = new URL("../", import.meta.url);
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith("@/")) {
      const base = new URL(specifier.slice(2), WEB_ROOT).href;
      const file = `${base}.ts`;
      const url = existsSync(fileURLToPath(file)) ? file : `${base}/index.ts`;
      return { url, shortCircuit: true };
    }
    if (specifier.startsWith(".") && !/\.[a-z]+$/.test(specifier)) {
      const base = new URL(specifier, context.parentURL).href;
      const file = `${base}.ts`;
      const url = existsSync(fileURLToPath(file)) ? file : `${base}/index.ts`;
      return { url, shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
});

class FakeSocket {
  static OPEN = 1;

  readyState = FakeSocket.OPEN;
  sent = [];
  listeners = new Map();

  addEventListener(type, listener) {
    const current = this.listeners.get(type) ?? new Set();
    current.add(listener);
    this.listeners.set(type, current);
  }

  removeEventListener(type, listener) {
    this.listeners.get(type)?.delete(listener);
  }

  throwOnSend = false;

  send(raw) {
    this.sent.push(JSON.parse(raw));
    if (this.throwOnSend) throw new Error("socket send failed");
  }

  emit(type, data) {
    for (const listener of this.listeners.get(type) ?? []) listener({ data });
  }
}

globalThis.WebSocket = FakeSocket;
const { setSocket } = await import("../lib/runtime-bridge/state.ts");
const {
  idempotencyKeyFor,
  isWsRequestPending,
  mutationRegistryStats,
  MutationRegistryCapacityError,
  reconcileWsMutation,
  resetWsMutationReconciliation,
  wsMutationRequest,
  wsRequest,
} = await import("../lib/net/ws-request.ts");
const { filesWsRequest, fileResponseMatchesOwner } = await import(
  "../lib/state/files-shared.ts"
);

after(() => resetWsMutationReconciliation());

test("stale cursor response is accepted after unrelated same-type frame", async () => {
  const socket = new FakeSocket();
  setSocket(socket);
  const response = filesWsRequest(
    "project_file_search",
    { project_id: "project-a", query: "needle", cursor: "expired", snapshot_id: "snap-a" },
    "project_file_search_result",
  );
  const [{ request_id: requestId }] = socket.sent;

  socket.emit("message", JSON.stringify({
    type: "project_file_search_result",
    data: {
      request_id: "00000000-0000-4000-8000-000000000001",
      action: "project_file_search",
      project_id: "project-b",
      status: "ready",
      snapshot_id: "snap-b",
    },
  }));
  socket.emit("message", JSON.stringify({
    type: "project_file_search_result",
    data: {
      request_id: requestId,
      action: "project_file_search",
      project_id: "project-a",
      query: "needle",
      status: "stale",
      error_code: "STALE_SNAPSHOT",
      snapshot_id: null,
      error: "search cursor expired",
    },
  }));

  assert.deepEqual(await response, {
    request_id: requestId,
    action: "project_file_search",
    project_id: "project-a",
    query: "needle",
    status: "stale",
    error_code: "STALE_SNAPSHOT",
    snapshot_id: null,
    error: "search cursor expired",
  });
});

test("owner mismatch remains rejected for stale responses", () => {
  assert.equal(fileResponseMatchesOwner(
    { project_id: "project-b", status: "stale", snapshot_id: null },
    { project_id: "project-a", snapshot_id: "snap-a" },
  ), false);
  assert.equal(fileResponseMatchesOwner(
    { project_id: "project-a", status: "stale", snapshot_id: null },
    { project_id: "project-a", snapshot_id: "snap-a" },
  ), true);
  assert.equal(fileResponseMatchesOwner(
    { project_id: "project-a", status: "stale", snapshot_id: "snap-other" },
    { project_id: "project-a", snapshot_id: "snap-a" },
  ), false);
});

test("close and send failure clean the exact pending request", async () => {
  const socket = new FakeSocket();
  setSocket(socket);
  const closed = wsRequest(
    "project_file_read",
    { project_id: "project-a", path: "README.md" },
    "project_file_read_result",
    { requestId: true },
  );
  const [{ request_id: closedId }] = socket.sent;
  assert.equal(isWsRequestPending(closedId, "project_file_read"), true);
  socket.emit("close");
  assert.equal(await closed, null);
  assert.equal(isWsRequestPending(closedId, "project_file_read"), false);

  socket.throwOnSend = true;
  const failed = wsRequest(
    "project_file_read",
    { project_id: "project-a", path: "README.md" },
    "project_file_read_result",
    { requestId: true },
  );
  const [{ request_id: failedId }] = socket.sent.slice(-1);
  assert.equal(await failed, null);
  assert.equal(isWsRequestPending(failedId, "project_file_read"), false);
});

test("mutation retries keep one key until a terminal receipt", async () => {
  const payload = { project_id: "project-a", path: "notes.txt", content: "v1" };
  const key = idempotencyKeyFor("project_file_write", payload);
  assert.equal(idempotencyKeyFor("project_file_write", payload), key);
  let attempts = 0;
  const result = await wsMutationRequest(
    key,
    async () => {
      attempts += 1;
      return attempts === 1 ? null : { status: "ready", operation_id: "op-1" };
    },
  );
  assert.equal(result?.operation_id, "op-1");
  assert.equal(attempts, 2);
  assert.notEqual(idempotencyKeyFor("project_file_write", payload), key);
});

test("large payload summaries stay bounded and in-progress keys remain stable", async () => {
  const content = "x".repeat(5 * 1024 * 1024);
  const before = mutationRegistryStats();
  const payloads = Array.from({ length: 4 }, (_, index) => ({
    project_id: "project-large",
    path: `notes-${index}.txt`,
    content,
  }));
  for (const payload of payloads) {
    const key = idempotencyKeyFor("project_file_write", payload);
    assert.equal(idempotencyKeyFor("project_file_write", payload), key);
    await wsMutationRequest(key, async () => ({
      status: "in_progress",
      operation_id: `op-${key}`,
    }), { maxAttempts: 1, deadlineMs: 0, reconcile: false });
    assert.equal(idempotencyKeyFor("project_file_write", payload), key);
  }
  const after = mutationRegistryStats();
  assert.ok(after.entries - before.entries <= 4);
  assert.ok(after.bytes - before.bytes < 64 * 1024);
  assert.equal(after.pending, 0);
});

test("registry refuses unfinished operations at capacity without growing", async () => {
  const before = mutationRegistryStats();
  const keys = [];
  const socket = new FakeSocket();
  setSocket(socket);
  let refused = false;
  for (let index = 0; index < 256; index += 1) {
    try {
      const key = idempotencyKeyFor("project_file_write", {
        project_id: "project-capacity",
        path: `file-${index}.txt`,
        content: "pending",
      });
      keys.push(key);
      await wsMutationRequest(key, async () => ({
        status: "in_progress",
        operation_id: `op-${key}`,
      }), { maxAttempts: 1, deadlineMs: 0, reconcile: false });
    } catch (error) {
      assert.ok(error instanceof MutationRegistryCapacityError);
      refused = true;
      break;
    }
  }
  assert.equal(refused, true);
  const after = mutationRegistryStats();
  assert.ok(after.entries <= 128);
  assert.ok(after.bytes <= 64 * 1024);
  assert.equal(after.pending, 0);
  assert.ok(after.entries >= before.entries);
  assert.ok(keys.length + before.entries <= 128);

  for (const key of keys) reconcileWsMutation(key);
  await new Promise((resolve) => setTimeout(resolve, 0));
  for (const frame of socket.sent) {
    if (frame.action !== "project_file_operation_status") continue;
    socket.emit("message", JSON.stringify({
      type: "project_file_operation_status_result",
      data: {
        request_id: frame.request_id,
        action: "project_file_operation_status",
        project_id: frame.project_id,
        operation_action: frame.operation_action,
        idempotency_key: frame.idempotency_key,
        status: "ready",
        operation_id: frame.operation_id,
      },
    }));
  }
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.ok(mutationRegistryStats().entries <= before.entries);
});

test("in-progress mutation reaches recovery_required at its deadline", async () => {
  // Reuse the first retained identity from the preceding capacity test so
  // the test does not need to allocate another registry entry.
  const payload = { project_id: "project-capacity", path: "file-0.txt", content: "pending" };
  const key = idempotencyKeyFor("project_file_write", payload);
  const result = await wsMutationRequest(key, async () => ({
    status: "in_progress", operation_id: "op-deadline",
  }), { maxAttempts: 1, deadlineMs: 0, reconcile: false });
  assert.equal(result?.status, "recovery_required");
  assert.equal(result?.error_code, "RECOVERY_REQUIRED");
  assert.equal(result?.operation_id, "op-deadline");
  assert.equal(idempotencyKeyFor("project_file_write", payload), key);
});

test("aborting a request retains its key for a later durable replay", async () => {
  // Reuse a retained identity from the capacity test; no new registry entry
  // is needed to verify that an abort preserves the durable key.
  const payload = { project_id: "project-capacity", path: "file-1.txt", content: "pending" };
  const key = idempotencyKeyFor("project_file_write", payload);
  let serverCalls = 0;
  let release;
  const firstController = new AbortController();
  const first = wsMutationRequest(key, async () => new Promise((resolve) => {
    serverCalls += 1;
    release = resolve;
  }), { signal: firstController.signal, maxAttempts: 1 });
  const second = wsMutationRequest(key, async () => {
    serverCalls += 1;
    return { status: "ready", operation_id: "unexpected-second-call" };
  }, { maxAttempts: 1 });
  assert.notEqual(first, second, "each caller receives its own subscription promise");
  firstController.abort();
  release({ status: "ready", operation_id: "op-abort-replay" });
  assert.equal(await first, null);
  const replay = await second;
  assert.equal(replay?.operation_id, "op-abort-replay");
  assert.equal(serverCalls, 1);
  assert.notEqual(idempotencyKeyFor("project_file_write", payload), key);
});

test("reconciliation polls a retained key without replaying write content", async () => {
  const payload = { project_id: "project-reconcile", path: "pending.txt", content: "v1" };
  const key = idempotencyKeyFor("project_file_write", payload);
  const socket = new FakeSocket();
  setSocket(socket);
  reconcileWsMutation(key);
  await new Promise((resolve) => setTimeout(resolve, 0));
  const frame = socket.sent.at(-1);
  assert.equal(frame.action, "project_file_operation_status");
  assert.equal(frame.content, undefined);
  socket.emit("message", JSON.stringify({
    type: "project_file_operation_status_result",
    data: {
      request_id: frame.request_id,
      action: "project_file_operation_status",
      project_id: payload.project_id,
      operation_action: "project_file_write",
      idempotency_key: key,
      status: "in_progress",
      operation_id: "op-reconcile",
    },
  }));
  await new Promise((resolve) => setTimeout(resolve, 300));
  const terminalFrame = socket.sent.at(-1);
  assert.equal(terminalFrame.action, "project_file_operation_status");
  socket.emit("message", JSON.stringify({
    type: "project_file_operation_status_result",
    data: {
      request_id: terminalFrame.request_id,
      action: "project_file_operation_status",
      project_id: payload.project_id,
      operation_action: "project_file_write",
      idempotency_key: key,
      status: "ready",
      operation_id: "op-reconcile",
    },
  }));
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.notEqual(idempotencyKeyFor("project_file_write", payload), key);
});

test("status query errors retain the key until an identified terminal receipt", async () => {
  const payload = { project_id: "project-status-error", path: "pending.txt", content: "v1" };
  const key = idempotencyKeyFor("project_file_write", payload);
  const socket = new FakeSocket();
  setSocket(socket);
  reconcileWsMutation(key);
  await new Promise((resolve) => setTimeout(resolve, 0));
  let frame = socket.sent.at(-1);
  socket.emit("message", JSON.stringify({
    type: "project_file_operation_status_result",
    data: {
      request_id: frame.request_id,
      action: "project_file_operation_status",
      project_id: payload.project_id,
      operation_action: "project_file_write",
      idempotency_key: key,
      status: "error",
      error_code: "AUTH_REQUIRED",
    },
  }));
  assert.equal(idempotencyKeyFor("project_file_write", payload), key);
  await new Promise((resolve) => setTimeout(resolve, 300));
  frame = socket.sent.at(-1);
  socket.emit("message", JSON.stringify({
    type: "project_file_operation_status_result",
    data: {
      request_id: frame.request_id,
      action: "project_file_operation_status",
      project_id: payload.project_id,
      operation_action: "project_file_write",
      idempotency_key: key,
      status: "ready",
      operation_id: "op-status-error",
    },
  }));
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.notEqual(idempotencyKeyFor("project_file_write", payload), key);
});

test("reconciliation rejects a terminal receipt for another operation", async () => {
  const payload = { project_id: "project-operation-mismatch", path: "same.txt", content: "v1" };
  const key = idempotencyKeyFor("project_file_write", payload);
  const socket = new FakeSocket();
  setSocket(socket);
  reconcileWsMutation(key);
  await new Promise((resolve) => setTimeout(resolve, 0));
  const firstFrame = socket.sent.at(-1);
  socket.emit("message", JSON.stringify({
    type: "project_file_operation_status_result",
    data: {
      request_id: firstFrame.request_id,
      action: "project_file_operation_status",
      project_id: payload.project_id,
      operation_action: "project_file_write",
      idempotency_key: key,
      status: "in_progress",
      operation_id: "op-expected",
    },
  }));
  await new Promise((resolve) => setTimeout(resolve, 300));
  const secondFrame = socket.sent.at(-1);
  socket.emit("message", JSON.stringify({
    type: "project_file_operation_status_result",
    data: {
      request_id: secondFrame.request_id,
      action: "project_file_operation_status",
      project_id: payload.project_id,
      operation_action: "project_file_write",
      idempotency_key: key,
      status: "ready",
      operation_id: "op-other",
    },
  }));
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(idempotencyKeyFor("project_file_write", payload), key);
  await new Promise((resolve) => setTimeout(resolve, 300));
  const thirdFrame = socket.sent.at(-1);
  socket.emit("message", JSON.stringify({
    type: "project_file_operation_status_result",
    data: {
      request_id: thirdFrame.request_id,
      action: "project_file_operation_status",
      project_id: payload.project_id,
      operation_action: "project_file_write",
      idempotency_key: key,
      status: "ready",
      operation_id: "op-expected",
    },
  }));
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.notEqual(idempotencyKeyFor("project_file_write", payload), key);
});

test("all shared callers detach independently while one receipt completes", async () => {
  const payload = { project_id: "project-shared", path: "same.txt", content: "v1" };
  const key = idempotencyKeyFor("project_file_write", payload);
  let release;
  let sends = 0;
  const callers = [];
  const controllers = [];
  callers.push(wsMutationRequest(key, async () => new Promise((resolve) => {
    sends += 1;
    release = resolve;
  }), { maxAttempts: 1 }));
  for (let index = 1; index < 128; index += 1) {
    const controller = new AbortController();
    controllers.push(controller);
    callers.push(wsMutationRequest(key, async () => {
      sends += 1;
      return { status: "ready", operation_id: "unexpected" };
    }, { signal: controller.signal }));
  }
  for (const controller of controllers) controller.abort();
  release({ status: "ready", operation_id: "op-shared" });
  const results = await Promise.all(callers);
  assert.equal(results.filter((result) => result?.status === "ready").length, 1);
  assert.equal(results.filter((result) => result === null).length, 127);
  assert.equal(sends, 1);
});

test("wsMutationRequest automatically starts status reconciliation", async () => {
  const payload = { project_id: "project-auto-reconcile", path: "same.txt", content: "v1" };
  const key = idempotencyKeyFor("project_file_write", payload);
  const socket = new FakeSocket();
  setSocket(socket);
  const result = await wsMutationRequest(key, async () => ({
    status: "in_progress", operation_id: "op-auto",
  }), { maxAttempts: 1, deadlineMs: 0 });
  assert.equal(result?.status, "recovery_required");
  await new Promise((resolve) => setTimeout(resolve, 0));
  const frame = socket.sent.at(-1);
  assert.equal(frame.action, "project_file_operation_status");
  socket.emit("message", JSON.stringify({
    type: "project_file_operation_status_result",
    data: {
      request_id: frame.request_id,
      action: "project_file_operation_status",
      project_id: payload.project_id,
      operation_action: "project_file_write",
      idempotency_key: key,
      status: "ready",
      operation_id: "op-auto",
    },
  }));
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.notEqual(idempotencyKeyFor("project_file_write", payload), key);
});

test("pre-aborted callers do not invoke the durable request", async () => {
  const payload = { project_id: "project-pre-abort", path: "same.txt", content: "v1" };
  const key = idempotencyKeyFor("project_file_write", payload);
  const controller = new AbortController();
  controller.abort();
  let sends = 0;
  assert.equal(await wsMutationRequest(key, async () => {
    sends += 1;
    return { status: "ready", operation_id: "unexpected" };
  }, { signal: controller.signal }), null);
  assert.equal(sends, 0);
  assert.equal(idempotencyKeyFor("project_file_write", payload), key);
});

test("turn reconciliation uses its own receipt action and exact operation", async () => {
  const payload = { session_id: "session-turn", msg_id: "turn-1" };
  const key = idempotencyKeyFor("revert_turn:session-turn", payload);
  const socket = new FakeSocket();
  setSocket(socket);
  reconcileWsMutation(key);
  reconcileWsMutation(key);
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(socket.sent.length, 1);
  const firstFrame = socket.sent[0];
  assert.equal(firstFrame.action, "turn_operation_status");
  socket.emit("message", JSON.stringify({
    type: "turn_operation_status_result",
    data: {
      request_id: firstFrame.request_id,
      action: "turn_operation_status",
      session_id: payload.session_id,
      msg_id: payload.msg_id,
      operation_action: "revert_turn",
      idempotency_key: key,
      status: "in_progress",
      operation_id: "turn-op-1",
    },
  }));
  await new Promise((resolve) => setTimeout(resolve, 300));
  const secondFrame = socket.sent.at(-1);
  socket.emit("message", JSON.stringify({
    type: "turn_operation_status_result",
    data: {
      request_id: secondFrame.request_id,
      action: "turn_operation_status",
      session_id: payload.session_id,
      msg_id: payload.msg_id,
      operation_action: "revert_turn",
      idempotency_key: key,
      status: "ready",
      operation_id: "turn-op-1",
    },
  }));
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.notEqual(idempotencyKeyFor("revert_turn:session-turn", payload), key);
});

test("turn unknown receipts remain retained until an identified terminal", async () => {
  const payload = { session_id: "session-turn-unknown", msg_id: "turn-2" };
  const key = idempotencyKeyFor("reapply_turn:session-turn-unknown", payload);
  const socket = new FakeSocket();
  setSocket(socket);
  reconcileWsMutation(key);
  await new Promise((resolve) => setTimeout(resolve, 0));
  const firstFrame = socket.sent.at(-1);
  socket.emit("message", JSON.stringify({
    type: "turn_operation_status_result",
    data: {
      request_id: firstFrame.request_id,
      action: "turn_operation_status",
      session_id: payload.session_id,
      msg_id: payload.msg_id,
      operation_action: "reapply_turn",
      idempotency_key: key,
      status: "recovery_required",
      error_code: "RECEIPT_UNAVAILABLE",
    },
  }));
  await new Promise((resolve) => setTimeout(resolve, 300));
  assert.equal(idempotencyKeyFor("reapply_turn:session-turn-unknown", payload), key);
  const secondFrame = socket.sent.at(-1);
  socket.emit("message", JSON.stringify({
    type: "turn_operation_status_result",
    data: {
      request_id: secondFrame.request_id,
      action: "turn_operation_status",
      session_id: payload.session_id,
      msg_id: payload.msg_id,
      operation_action: "reapply_turn",
      idempotency_key: key,
      status: "ready",
      operation_id: "turn-op-unknown",
    },
  }));
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.notEqual(idempotencyKeyFor("reapply_turn:session-turn-unknown", payload), key);
});

test("reset prevents a pending operation from starting a late reconciler", async () => {
  const payload = { project_id: "project-reset", path: "same.txt", content: "v1" };
  const key = idempotencyKeyFor("project_file_write", payload);
  const socket = new FakeSocket();
  setSocket(socket);
  let release;
  const operation = wsMutationRequest(key, async () => new Promise((resolve) => {
    release = resolve;
  }));
  reconcileWsMutation(key);
  resetWsMutationReconciliation();
  release({ status: "ready", operation_id: "op-reset" });
  assert.equal((await operation)?.status, "ready");
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(socket.sent.length, 0);
});
