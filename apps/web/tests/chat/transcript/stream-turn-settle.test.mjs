/**
 * Which send a chat frame answers: rejections that name (or do not name) the
 * pending send, an error of a turn already under way, a failed function Retry,
 * acks naming another request, and sends left without an ACK when the socket
 * closes. A turn's own rows come from numbered updates.
 */
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import test from "node:test";
import { fileURLToPath } from "node:url";

registerHooks({
  resolve(specifier, context, nextResolve) {
    const base = specifier.startsWith("@/")
      ? new URL(`../../../${specifier.slice(2)}`, import.meta.url).href
      : specifier.startsWith(".") && !/\.[a-z]+$/.test(specifier)
        ? new URL(specifier, context.parentURL).href
        : null;
    if (base) {
      for (const suffix of [".ts", ".tsx", "/index.ts"]) {
        if (existsSync(fileURLToPath(base + suffix))) return { url: base + suffix, shortCircuit: true };
      }
    }
    return nextResolve(specifier, context);
  },
});

const values = new Map();
globalThis.window = { addEventListener: () => {}, dispatchEvent: () => {}, location: { pathname: "/chat" } };
globalThis.localStorage = {
  getItem: (key) => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, String(value)),
  removeItem: (key) => values.delete(key),
};
globalThis.requestAnimationFrame = undefined;

const { useSessionStore, selectRow } = await import("../../../lib/session-store/index.ts");
const { applyChatWsMessage, appendLocalUserTurn } = await import("../../../lib/net/ws/chat-stream.ts");
const { transcriptRows } = await import("../../_support/transcript-rows.mjs");
const rows = transcriptRows(useSessionStore, selectRow);
const { settleSendsOnDisconnect } = await import("../../../lib/net/ws/unacked-sends.ts");
const pending = await import("../../../lib/chat/send/pending-user-text.ts");

const response = (sid, data) => applyChatWsMessage({ type: "chat_response", data: { session_id: sid, ...data } });
let view = "";
const row = (id) => rows.readRow(view, id);

function startTurn(sid, msgId) {
  view = sid;
  rows.putRows(sid, [
    { id: msgId, role: "user", rowKind: "user", content: "go", status: "done" },
    { id: `${msgId}_reply`, role: "assistant", rowKind: "assistant", content: "", status: "running" },
  ]);
}

function stage(sid, requestId) {
  view = sid;
  const id = `pending_${sid}`;
  pending.setPendingUserText(sid, "hello", Date.now(), { messageId: id, requestId });
  appendLocalUserTurn(sid, id, "hello", undefined, Date.now(), "pending", undefined, requestId);
  return id;
}

test("a rejection naming another request leaves the pending send alone", () => {
  const id = stage("st_req", "req-a");
  response("st_req", { type: "error", msg_id: "x1", client_request_id: "req-b", content: "nope" });
  assert.equal(row(id).status, "pending");
  assert.equal(pending.getPendingUserText("st_req"), "hello");
  response("st_req", { type: "error", msg_id: "x2", client_request_id: "req-a", content: "nope" });
  assert.equal(row(id).status, "error");
  assert.equal(pending.getPendingUserText("st_req"), undefined);

  // A frame naming no request still answers the session's pending send.
  const legacy = stage("st_legacy");
  response("st_legacy", { type: "error", msg_id: "x3", content: "nope" });
  assert.equal(row(legacy).status, "error");
});

test("an error of a turn already under way is not a rejection of the pending send", () => {
  startTurn("st_turn", "m4");
  const id = stage("st_turn");
  response("st_turn", { type: "error", msg_id: "m4", row_id: "m4_reply", content: "provider failed" });
  assert.equal(row(id).status, "pending");
  pending.clearPendingUserText("st_turn");
});

test("a failed function Retry answers its request: no row, no rejected send", () => {
  const id = stage("st_retry");
  response("st_retry", { type: "error", msg_id: "rnd", retry_request_id: "r1", content: "Retry failed", display: "runtime" });
  assert.equal(row("rnd_reply"), undefined);
  assert.equal(row(id).status, "pending");
  pending.clearPendingUserText("st_retry");
});

test("an ack naming another request does not consume the pending send", () => {
  const id = stage("st_ack", "req-a");
  applyChatWsMessage({ type: "chat_ack", data: { session_id: "st_ack", msg_id: "u9", client_request_id: "req-b" } });
  assert.equal(row("u9"), undefined);
  assert.equal(row(id).status, "pending");
  applyChatWsMessage({ type: "chat_ack", data: { session_id: "st_ack", msg_id: "u10", client_request_id: "req-a" } });
  assert.equal(row(id), undefined);
  assert.equal(row("u10").status, "done");
  pending.clearPendingUserText("st_ack");
});

test("a closed socket marks sends left without an ACK as not sent", () => {
  const id = stage("st_close", "req-z");
  settleSendsOnDisconnect();
  assert.equal(row(id).status, "error");
  assert.equal(row(id).errorReason, "disconnected");
  assert.equal(pending.getPendingUserText("st_close"), undefined);
});
