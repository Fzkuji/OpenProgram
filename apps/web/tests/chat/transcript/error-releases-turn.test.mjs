/**
 * A reply that ends in error releases the session's running task on this
 * client at once, so the next message sends instead of queueing behind a
 * turn that is over. A later error naming a settled or newer turn leaves
 * the slot alone.
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
const { transcriptRows } = await import("../../_support/transcript-rows.mjs");
const rows = transcriptRows(useSessionStore, selectRow);
const { applyChatWsMessage } = await import("../../../lib/net/ws/chat-stream.ts");

// The server names the reply row each frame of a turn answers.
const response = (sid, data) => applyChatWsMessage({ type: "chat_response", data: { session_id: sid, row_id: `${data.msg_id}_reply`, ...data } });
const settle = () => new Promise((resolve) => setTimeout(resolve, 20));

function startTurn(sid, msgId, executionId) {
  rows.putRows(sid, [
    { id: msgId, role: "user", rowKind: "user", content: "go", status: "done" },
    { id: `${msgId}_reply`, role: "assistant", rowKind: "assistant", content: "", status: "running" },
  ]);
  useSessionStore.getState().setRunningTaskFor(sid, { session_id: sid, msg_id: msgId, execution_id: executionId }, "never");
}

test("an error frame for the running turn frees the session", async () => {
  startTurn("s_err", "m1", "e1");
  // The frame comes first; the reply's error status follows on the next
  // update tick, as the provider_error recording shows.
  response("s_err", { type: "error", msg_id: "m1", content: "provider failed" });
  await settle();
  assert.equal(useSessionStore.getState().runningTasks.s_err, undefined);
  rows.patchRow("s_err", "m1_reply", { status: "error" });
});

test("an error of an earlier turn leaves the newer running turn alone", async () => {
  startTurn("s_newer", "m1", "e1");
  startTurn("s_newer", "m2", "e2");
  response("s_newer", { type: "error", msg_id: "m1", content: "late failure" });
  await settle();
  assert.equal(useSessionStore.getState().runningTasks.s_newer?.execution_id, "e2");
});

test("a late error after the reply settled changes nothing", async () => {
  startTurn("s_done", "m3", "e3");
  rows.patchRow("s_done", "m3_reply", { status: "done" });
  response("s_done", { type: "result", msg_id: "m3", content: "ok" });
  useSessionStore.getState().setRunningTaskFor("s_done", { session_id: "s_done", msg_id: "m3", execution_id: "e3" }, "never");
  response("s_done", { type: "error", msg_id: "m3", content: "compaction failed" });
  await settle();
  assert.equal(rows.readRow("s_done", "m3_reply").status, "done");
  assert.equal(useSessionStore.getState().runningTasks.s_done?.execution_id, "e3");
});
