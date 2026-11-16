/**
 * A reply frame without session_id belongs to the run that owns its msg_id,
 * never to the conversation on screen: after a reload, a run started earlier
 * must not stream into a new chat or another conversation the user opened.
 */
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
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
globalThis.requestAnimationFrame = undefined;

const { useSessionStore, selectRow } = await import("../../../lib/session-store/index.ts");
const { flushTextFrames } = await import("../../../lib/net/ws/text-frames.ts");
const { transcriptRows } = await import("../../_support/transcript-rows.mjs");
const rows = transcriptRows(useSessionStore, selectRow);

const { applyChatWsMessage } = await import("../../../lib/net/ws/chat-stream.ts");

const frame = (data) => applyChatWsMessage({ type: "chat_response", data });

test("a frame without session_id goes to the run that owns its msg_id", () => {
  const store = useSessionStore.getState();
  rows.putRows("s_run", [
    { id: "m1", role: "user", rowKind: "user", content: "apply", status: "done" },
    { id: "m1_reply", role: "assistant", rowKind: "assistant", content: "", status: "running", blocks: [] },
  ]);
  rows.putRow("s_open", { id: "o1", role: "user", rowKind: "user", content: "hello", status: "done" });
  store.setRunningTaskFor("s_run", { session_id: "s_run", msg_id: "m1" }, "never");
  store.setCurrentConv("s_open");

  frame({ type: "stream_event", msg_id: "m1", row_id: "m1_reply", item_id: "b0", item_index: 0, offset: 0, event: { type: "text", text: "Applying" } });
  flushTextFrames();

  const s = useSessionStore.getState();
  assert.deepEqual(s.messageOrder.s_open, ["o1"], "the open conversation gains no rows");
  assert.equal(rows.readRow("s_run", "m1_reply").content, "Applying");
});

test("a frame no run owns is dropped instead of landing in a new chat", () => {
  const store = useSessionStore.getState();
  store.setCurrentDraft("draft_new");
  frame({ type: "stream_event", msg_id: "lost", row_id: "lost_reply", item_id: "b0", item_index: 0, offset: 0, event: { type: "text", text: "stray" } });
  frame({ type: "result", msg_id: "lost", content: "stray" });
  flushTextFrames();
  const s = useSessionStore.getState();
  assert.equal(s.messageOrder.draft_new, undefined);
});

test("a first message starting with Fix/Run is a chat turn, not a function run", async () => {
  const { setPendingUserText } = await import("../../../lib/chat/send/pending-user-text.ts");
  setPendingUserText("s_fix", "Fix the date bug in app.js");
  applyChatWsMessage({ type: "chat_ack", data: { session_id: "s_fix", msg_id: "f1" } });
  assert.equal(rows.readRow("s_fix", "f1").role, "user");
  assert.equal(rows.readRow("s_fix", "f1").display, undefined);
  assert.deepEqual(useSessionStore.getState().messageOrder.s_fix, ["f1"]);
});
