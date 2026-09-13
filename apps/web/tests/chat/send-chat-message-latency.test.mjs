import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import { fileURLToPath } from "node:url";
import test from "node:test";

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith("@/")) {
      const base = new URL(`../../${specifier.slice(2)}`, import.meta.url).href;
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
  addEventListener() {}, removeEventListener() {}, dispatchEvent() {},
  location: { pathname: "/chat", hash: "", search: "" },
  history: { state: null, replaceState() {}, pushState() {} },
};
globalThis.document = {
  addEventListener() {}, removeEventListener() {}, getElementById() { return null; },
  querySelector() { return null; }, querySelectorAll() { return []; },
  createElement() { return { style: {}, classList: { add() {}, remove() {} }, setAttribute() {}, appendChild() {} }; },
};
globalThis.localStorage = {
  getItem: (key) => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, String(value)),
  removeItem: (key) => values.delete(key),
};
globalThis.WebSocket = { OPEN: 1 };

const { runtimeState, setSocket } = await import("../../lib/runtime-bridge/state.ts");
const { useSessionStore } = await import("../../lib/session-store/index.ts");
const pending = await import("../../lib/chat/pending-user-text.ts");
const { sendChatMessage } = await import("../../components/chat/composer/submit/send-chat-message.ts");

function reset(sid) {
  pending.clearPendingUserText(sid);
  pending.clearPendingFirstAck(sid);
  useSessionStore.setState({ messagesById: {}, messageOrder: {}, runningTasks: {}, currentSessionId: sid });
  runtimeState.currentSessionId = sid;
}

test("socket write immediately paints pending user row before delayed ACK", () => {
  const sid = "s_latency_ack";
  reset(sid);
  const frames = [];
  let sentCleanup = 0;
  setSocket({ readyState: 1, send(payload) { frames.push(JSON.parse(payload)); } });
  assert.equal(sendChatMessage({
    text: "delayed", sessionId: sid, thinking: "medium", toolsEnabled: true,
    webSearchEnabled: false, onSent: () => { sentCleanup += 1; },
  }), true);
  const state = useSessionStore.getState();
  assert.equal(frames.length, 1);
  assert.deepEqual(state.messageOrder[sid].length, 1);
  const row = state.messagesById[state.messageOrder[sid][0]];
  assert.equal(row.role, "user");
  assert.equal(row.content, "delayed");
  assert.equal(row.status, "pending");
  assert.equal(sentCleanup, 1);
});

test("socket failure leaves draft reservation and paints no optimistic row", () => {
  const sid = "s_latency_socket_failure";
  reset(sid);
  let sentCleanup = 0;
  setSocket({ readyState: 1, send() { throw new Error("closed during write"); } });
  assert.equal(sendChatMessage({
    text: "keep me", sessionId: sid, thinking: "medium", toolsEnabled: true,
    webSearchEnabled: false, onSent: () => { sentCleanup += 1; },
  }), false);
  assert.equal(useSessionStore.getState().messageOrder[sid]?.length ?? 0, 0);
  assert.equal(pending.getPendingUserText(sid), undefined);
  assert.equal(sentCleanup, 0);
});
