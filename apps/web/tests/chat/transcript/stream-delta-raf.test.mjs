/**
 * What chat frames still say on their own, now that numbered updates own every
 * server row: an ack gives the local send its server id, a refusal marks the
 * send, text goes into the held row by offset and once per frame, retry sets a
 * client-only field, and a turn's end asks for its files to open.
 */
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import test from "node:test";
import { fileURLToPath } from "node:url";

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith("@/")) {
      const base = new URL(`../../../${specifier.slice(2)}`, import.meta.url).href;
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

const values = new Map();
globalThis.window = {
  addEventListener: () => {},
  dispatchEvent: () => {},
  location: { pathname: "/chat" },
};
globalThis.localStorage = {
  getItem: (key) => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, String(value)),
  removeItem: (key) => values.delete(key),
};

const rafQueue = [];
let rafHandle = 1;
globalThis.requestAnimationFrame = (cb) => {
  const id = rafHandle++;
  rafQueue.push({ id, cb });
  return id;
};
globalThis.cancelAnimationFrame = (id) => {
  const i = rafQueue.findIndex((item) => item.id === id);
  if (i >= 0) rafQueue.splice(i, 1);
};

const { useSessionStore, selectRow } = await import("../../../lib/session-store/index.ts");
const { applyChatWsMessage, clearSessionByMsgId, appendLocalUserTurn } = await import("../../../lib/net/ws/chat-stream.ts");
const { flushTextFrames } = await import("../../../lib/net/ws/text-frames.ts");
const { clearPendingUserText, setPendingUserText } = await import("../../../lib/chat/send/pending-user-text.ts");
const { transcriptRows } = await import("../../_support/transcript-rows.mjs");
const rows = transcriptRows(useSessionStore, selectRow);

function reset(sid) {
  clearSessionByMsgId();
  clearPendingUserText(sid);
  useSessionStore.setState({ rowsByView: {}, overlaysByView: {}, messageOrder: {}, currentSessionId: sid });
}

function pendingSend(sid, id, text, options = {}) {
  setPendingUserText(sid, text, 123, { messageId: id, ...options });
  appendLocalUserTurn(sid, id, text, undefined, 123, "pending");
}

function runFrame() {
  const queue = rafQueue.splice(0);
  for (const { cb } of queue) cb();
}

function textFrame(sid, uid, rowId, offset, text, itemIndex = 0) {
  applyChatWsMessage({ type: "chat_response", data: {
    type: "stream_event", session_id: sid, msg_id: uid, row_id: rowId, item_id: `${rowId}_b${itemIndex}`,
    item_index: itemIndex, offset, event: { type: "text", text },
  } });
}

test("ACK gives the visible pending user row its server id and adds no reply", () => {
  const sid = "s_pending_ack";
  reset(sid);
  pendingSend(sid, "pending_local_ack", "hello");
  applyChatWsMessage({ type: "chat_ack", data: { session_id: sid, msg_id: "server_ack_1", text: "hello" } });
  assert.deepEqual(useSessionStore.getState().messageOrder[sid], ["server_ack_1"]);
  const row = rows.readRow(sid, "server_ack_1");
  assert.equal(row.content, "hello");
  assert.equal(row.status, "done");
});

test("run_active with attachments restores the captured draft and keeps a failed row", () => {
  const sid = "s_pending_attachment_error";
  let restored = 0;
  reset(sid);
  pendingSend(sid, "pending_attachment_error", "attach this", { hasAttachments: true, onReject: () => { restored += 1; } });
  applyChatWsMessage({ type: "chat_response", data: { type: "error", code: "run_active", session_id: sid, msg_id: "rejected_attachment" } });
  assert.equal(restored, 1);
  assert.equal(rows.readRow(sid, "pending_attachment_error").status, "error");
});

test("a rejected send keeps its old row and draws no reply", () => {
  const sid = "s_pending_newer_draft";
  reset(sid);
  pendingSend(sid, "pending_newer", "old rejected text", { onReject: () => {} });
  applyChatWsMessage({ type: "chat_response", data: { type: "error", session_id: sid, msg_id: "rejected_newer", reason: "provider" } });
  const row = rows.readRow(sid, "pending_newer");
  assert.equal(row.content, "old rejected text");
  assert.equal(row.status, "error");
  assert.deepEqual(useSessionStore.getState().messageOrder[sid], ["pending_newer"]);
});

test("ACK falls back to a local row when a read removed the pending row", () => {
  const sid = "s_pending_hydrate";
  reset(sid);
  pendingSend(sid, "pending_hydrate", "hello after reload");
  rows.removeRow(sid, "pending_hydrate");
  applyChatWsMessage({ type: "chat_ack", data: { session_id: sid, msg_id: "server_hydrate", text: "hello after reload" } });
  assert.deepEqual(useSessionStore.getState().messageOrder[sid], ["server_hydrate"]);
  assert.equal(rows.readRow(sid, "server_hydrate").content, "hello after reload");
});

test("ACK after the server's own row removes the local one", () => {
  const sid = "s_pending_duplicate_hydrate";
  reset(sid);
  pendingSend(sid, "pending_duplicate", "hello");
  rows.putRow(sid, { id: "server_duplicate", role: "user", rowKind: "user", content: "hello", status: "done" });
  applyChatWsMessage({ type: "chat_ack", data: { session_id: sid, msg_id: "server_duplicate", text: "hello" } });
  assert.deepEqual(useSessionStore.getState().messageOrder[sid], ["server_duplicate"]);
});

test("text frames for one row stamp the store once per animation frame and never twice", () => {
  const sid = "s_text", uid = "u_text", rid = `${uid}_reply`;
  reset(sid);
  rows.putRow(sid, { id: rid, role: "assistant", rowKind: "assistant", content: "", status: "running", blocks: [] });
  let writes = 0;
  const stop = useSessionStore.subscribe(() => { writes += 1; });
  textFrame(sid, uid, rid, 0, "Hel");
  textFrame(sid, uid, rid, 3, "lo");
  textFrame(sid, uid, rid, 0, "Hel");
  assert.equal(writes, 0);
  runFrame();
  stop();
  assert.equal(writes, 1);
  assert.equal(rows.readRow(sid, rid).content, "Hello");
});

test("a text frame for a row no view holds creates nothing", () => {
  const sid = "s_unknown";
  reset(sid);
  textFrame(sid, "u_unknown", "u_unknown_reply", 0, "lost");
  flushTextFrames();
  assert.equal(useSessionStore.getState().messageOrder[sid], undefined);
});

test("retry status is visible during the same turn and clears on new text and on completion", () => {
  const sid = "retry-status", uid = "retry-user", rid = uid + "_reply";
  reset(sid);
  rows.putRow(sid, { id: rid, role: "assistant", rowKind: "assistant", content: "", status: "running", blocks: [] });
  applyChatWsMessage({ type: "chat_response", data: { type: "stream_event", session_id: sid, msg_id: uid, row_id: rid,
    event: { type: "retry", attempt: 2, max_attempts: 3, reason: "transport", delay_ms: 1500, error: "ReadTimeout: no data in 120s" } } });
  const { receivedAt, ...retryStatus } = rows.readRow(sid, rid).retryStatus;
  assert.deepEqual(retryStatus, { attempt: 2, maxAttempts: 3, reason: "transport", delayMs: 1500, error: "ReadTimeout: no data in 120s" });
  // The countdown runs from when the notice arrived.
  assert.ok(Math.abs(Date.now() - receivedAt) < 5000);
  // A server row update keeps the client-only field.
  rows.patchRow(sid, rid, { content: "" });
  assert.ok(rows.readRow(sid, rid).retryStatus);
  textFrame(sid, uid, rid, 0, "Continuing");
  flushTextFrames();
  assert.equal(rows.readRow(sid, rid).retryStatus, undefined);
  applyChatWsMessage({ type: "chat_response", data: { type: "stream_event", session_id: sid, msg_id: uid, row_id: rid, event: { type: "retry", attempt: 3, max_attempts: 3, reason: "transport" } } });
  applyChatWsMessage({ type: "chat_response", data: { type: "result", session_id: sid, msg_id: uid, row_id: rid, content: "Done" } });
  assert.equal(rows.readRow(sid, rid).retryStatus, undefined);
});

test("only a foreground successful reply asks for its files to open, once", () => {
  const finish = (sid, id, type = "result") => {
    rows.putRow(sid, { id: `${id}_reply`, role: "assistant", rowKind: "assistant", content: "[File](/project/report.pdf)", status: "running" });
    applyChatWsMessage({ type: "chat_ack", data: { session_id: sid, msg_id: id } });
    applyChatWsMessage({ type: "chat_response", data: { type, session_id: sid, msg_id: id, row_id: `${id}_reply`, content: "[File](/project/report.pdf)" } });
    return rows.readRow(sid, `${id}_reply`);
  };
  reset("file-foreground");
  assert.equal(finish("file-background", "file-bg").autoPreviewFiles, undefined);
  assert.equal(finish("file-foreground", "file-error", "error").autoPreviewFiles, undefined);
  assert.equal(finish("file-foreground", "file-ok").autoPreviewFiles, true);
  useSessionStore.getState().applyLocalChange("file-foreground", { kind: "local_patch", rowId: "file-ok_reply", fields: { autoPreviewFiles: false } });
  applyChatWsMessage({ type: "chat_response", data: { type: "result", session_id: "file-foreground", msg_id: "file-ok", row_id: "file-ok_reply", content: "[File](/project/report.pdf)" } });
  assert.equal(rows.readRow("file-foreground", "file-ok_reply").autoPreviewFiles, false);
});
