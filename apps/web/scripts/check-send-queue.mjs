/**
 * Send-queue invariants — messages typed while a turn is running.
 *
 * Covers the three things that make the queue trustworthy rather than a
 * second place messages can vanish:
 *   1. queued entries drain ONE AT A TIME, in order, only when the
 *      session is idle;
 *   2. the drain is triggered by the running-task clear, with no polling;
 *   3. queues are keyed by session, so one session's turn ending never
 *      ships another session's queue.
 * Plus remove / promoteToHead, which back the two per-row actions.
 */
import assert from "node:assert/strict";

import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import { fileURLToPath } from "node:url";

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith("@/")) {
      const base = new URL(`../${specifier.slice(2)}`, import.meta.url).href;
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
globalThis.document = {
  addEventListener: () => {},
  removeEventListener: () => {},
  getElementById: () => null,
  querySelector: () => null,
  querySelectorAll: () => [],
  createElement: () => ({
    getContext: () => null,
    style: {},
    classList: { add() {}, remove() {} },
    setAttribute() {},
    appendChild() {},
  }),
  body: null,
};
globalThis.localStorage = {
  getItem: (key) => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, String(value)),
  removeItem: (key) => values.delete(key),
};
globalThis.WebSocket = { OPEN: 1 };

const sent = [];
const { setSocket } = await import("../lib/runtime-bridge/state.ts");
setSocket({ readyState: 1, send: (payload) => sent.push(JSON.parse(payload)) });

// Importing send-chat-message registers the real chat sender with the
// queue — the same wiring the app relies on.
await import("../components/chat/composer/submit/send-chat-message.ts");
const {
  useSendQueue,
  enqueueMessage,
  promoteToHead,
  requeueRejected,
  reconcileAfterSessionLoad,
} =
  await import("../lib/state/send-queue.ts");
const { useSessionStore } = await import("../lib/session-store/index.ts");
const { stopSession } = await import(
  "../components/chat/composer/submit/use-chat-submit.ts"
);
const { handleRunningTaskClear } = await import(
  "../lib/runtime-bridge/chat-handlers.ts"
);

const A = "sess_a";
const B = "sess_b";
const draft = (text) => ({
  text,
  thinking: "medium",
  toolsEnabled: true,
  webSearchEnabled: false,
  background: false,
});

const store = useSessionStore.getState();
const run = (sid) =>
  store.setRunningTaskFor(sid, { session_id: sid, msg_id: "m" });
const idle = (sid) => store.setRunningTaskFor(sid, null);
// The drain hangs off a microtask + dynamic import inside the store
// setter; let both settle before asserting.
const settle = async () => {
  for (let i = 0; i < 5; i++) await Promise.resolve();
  await new Promise((r) => setTimeout(r, 0));
};

/* --- 1. queued while running, drained in order, one at a time -------- */
run(A);
enqueueMessage(A, draft("first"));
enqueueMessage(A, draft("second"));
assert.equal(
  useSendQueue.getState().queues[A].length, 2,
  "both messages park in the queue while the turn runs",
);
assert.equal(sent.length, 0, "nothing goes out while the session is busy");

idle(A);
await settle();
assert.equal(sent.length, 1, "the turn ending sends exactly ONE queued message");
assert.equal(sent[0].text, "first", "the queue drains in the order typed");
assert.equal(
  useSendQueue.getState().queues[A].length, 1,
  "the rest waits for the next turn to end",
);

/* --- 2. the second one waits for ITS turn to end --------------------- */
run(A);
await settle();
assert.equal(sent.length, 1, "a queued message never jumps a running turn");
idle(A);
await settle();
assert.equal(sent.length, 2, "the next turn ending ships the next message");
assert.equal(sent[1].text, "second");
assert.equal(
  useSendQueue.getState().queues[A], undefined,
  "an emptied queue drops its session entry",
);

/* --- 3. queues are per session --------------------------------------- */
sent.length = 0;
run(A);
run(B);
enqueueMessage(A, draft("for-a"));
enqueueMessage(B, draft("for-b"));
idle(B);
await settle();
assert.equal(sent.length, 1, "only the idle session drains");
assert.equal(sent[0].session_id, B, "session B's turn ending sends B's message");
assert.equal(
  useSendQueue.getState().queues[A].length, 1,
  "session A's queue is untouched while A is still running",
);
idle(A);
await settle();
assert.equal(sent[1].text, "for-a");

/* --- 4. remove (撤回) ------------------------------------------------- */
sent.length = 0;
run(A);
enqueueMessage(A, draft("keep"));
const dropId = enqueueMessage(A, draft("drop"));
useSendQueue.getState().remove(A, dropId);
idle(A);
await settle();
assert.equal(sent.length, 1);
assert.equal(sent[0].text, "keep", "a removed message is never sent");
assert.equal(useSendQueue.getState().queues[A], undefined);

/* --- 5. promoteToHead (停止当前并立即发送) ---------------------------- */
sent.length = 0;
run(A);
enqueueMessage(A, draft("was-first"));
const jumpId = enqueueMessage(A, draft("jump-me"));
promoteToHead(A, jumpId);
assert.deepEqual(
  useSendQueue.getState().queues[A].map((q) => q.text),
  ["jump-me", "was-first"],
  "promoting moves an entry to the front and keeps the others behind it",
);
idle(A);
await settle();
assert.equal(sent[0].text, "jump-me", "the promoted message is the one sent");

/* --- 6. run_active race → silent re-queue at the front ---------------- */
sent.length = 0;
run(A);
requeueRejected(A, "rejected-by-race");
assert.deepEqual(
  useSendQueue.getState().queues[A].map((q) => q.text),
  ["rejected-by-race", "was-first"],
  "a rejected turn re-queues ahead of messages typed after it",
);
idle(A);
await settle();
assert.equal(sent[0].text, "rejected-by-race");

/* --- 7. repeated clear before ACK still sends only one -------------- */
sent.length = 0;
useSendQueue.setState({ queues: {} });
run(A);
enqueueMessage(A, draft("clear-first"));
enqueueMessage(A, draft("clear-second"));
idle(A);
idle(A);
await settle();
assert.deepEqual(
  sent.map((m) => m.text),
  ["clear-first"],
  "duplicate idle notifications before ACK must not dispatch the next entry",
);
assert.deepEqual(
  useSendQueue.getState().queues[A].map((q) => q.text),
  ["clear-second"],
  "the second entry waits for the first queued turn to finish",
);

/* --- 8. idle→idle is not a turn-completion signal ------------------ */
sent.length = 0;
useSendQueue.setState({ queues: {} });
idle(A);
await settle();
enqueueMessage(A, draft("stale-idle"));
idle(A);
await settle();
assert.equal(
  sent.length,
  0,
  "setting an already-idle session to idle must not drain its queue",
);
assert.equal(useSendQueue.getState().queues[A][0].text, "stale-idle");

/* --- 9. reconnect waits for authoritative idle, then retries -------- */
sent.length = 0;
useSendQueue.setState({ queues: {} });
enqueueMessage(A, draft("after-reconnect"));
setSocket({ readyState: 0, send: () => assert.fail("closed socket wrote") });
useSendQueue.getState().drain(A);
assert.equal(useSendQueue.getState().queues[A][0].text, "after-reconnect");
setSocket({ readyState: 1, send: (payload) => sent.push(JSON.parse(payload)) });
reconcileAfterSessionLoad(A, true);
assert.equal(sent.length, 0, "an active restored session must not drain");
reconcileAfterSessionLoad(A, false);
await settle();
assert.deepEqual(sent.map((m) => m.text), ["after-reconnect"]);
assert.equal(useSendQueue.getState().queues[A], undefined);

/* --- 10. attached drafts are retained, never split into text queue -- */
const attachedId = enqueueMessage(A, draft("caption with image"), 1);
assert.equal(attachedId, null, "an attached draft must not enter the text queue");
assert.equal(useSendQueue.getState().queues[A], undefined);

/* --- 11. stop-now drains the queue at 0ms (Claude Code) ----------- */
sent.length = 0;
useSendQueue.setState({ queues: {} });
run(A);
const stoppedId = enqueueMessage(A, draft("send-after-stop"));
promoteToHead(A, stoppedId);
const stopFrames = [];
stopSession(A, (payload) => {
  stopFrames.push(payload);
  return true;
});
await settle();
assert.equal(stopFrames.length, 1, "cancel is sent immediately");
assert.equal(stopFrames[0].action, "execution.cancel");
assert.equal(stopFrames[0].execution_id, "m_reply");
assert.deepEqual(
  sent.map((m) => m.text),
  ["send-after-stop"],
  "stopSession must send the promoted queued message at 0ms",
);
const sentOnce = sent.length;
const afterStop = useSessionStore.getState().runningTasks[A];
assert.ok(afterStop, "stop-and-send must leave occupancy for the new turn");
handleRunningTaskClear(A);
await settle();
assert.equal(
  sent.length,
  sentOnce,
  "handleRunningTaskClear after stop must not send the queued message a second time",
);
assert.ok(
  useSessionStore.getState().runningTasks[A],
  "unscoped clear for the old turn must not idle the new turn",
);
handleRunningTaskClear(A, { execution_id: "m_reply", msg_id: "m" });
await settle();
assert.ok(
  useSessionStore.getState().runningTasks[A],
  "clear naming the interrupted turn must not idle the new turn",
);

console.log("check-send-queue: ok");
