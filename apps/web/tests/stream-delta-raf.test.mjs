/**
 * text/thinking stream deltas coalesce to one store stamp per animation
 * frame. A tool/status/finalize event flushes that rid first so the
 * card cannot land before the body it followed.
 */
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import test from "node:test";
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

const { useSessionStore } = await import("../lib/session-store/index.ts");
const { applyChatWsMessage, clearSessionByMsgId } = await import(
  "../lib/net/chat-stream.ts"
);
const realUpdateMessage = useSessionStore.getState().updateMessage;

const SID = "s_raf";
const UID = "u_raf";
const RID = `${UID}_reply`;

function reply() {
  return useSessionStore.getState().messagesById[RID];
}

function send(event, sid = SID, uid = UID) {
  applyChatWsMessage({
    type: "chat_response",
    data: { type: "stream_event", session_id: sid, msg_id: uid, event },
  });
}

function runFrame() {
  const batch = rafQueue.splice(0);
  for (const item of batch) item.cb(0);
}

function countReplyStamps() {
  const orig = useSessionStore.getState().updateMessage;
  let n = 0;
  useSessionStore.setState({
    updateMessage(sid, id, patch) {
      if (id === RID) n += 1;
      return orig(sid, id, patch);
    },
  });
  return {
    get count() {
      return n;
    },
    restore() {
      useSessionStore.setState({ updateMessage: orig });
    },
  };
}

function reset() {
  rafQueue.length = 0;
  useSessionStore.setState({ updateMessage: realUpdateMessage });
  clearSessionByMsgId();
  useSessionStore.setState({
    messagesById: {},
    messageOrder: {},
    currentSessionId: SID,
  });
  applyChatWsMessage({
    type: "chat_ack",
    data: { session_id: SID, msg_id: UID },
  });
}

test("two text deltas in one frame stamp the store once", () => {
  reset();
  const stamps = countReplyStamps();
  send({ type: "text", text: "hel" });
  send({ type: "text", text: "lo" });
  assert.equal(reply()?.content ?? "", "");
  assert.equal(stamps.count, 0);
  runFrame();
  assert.equal(reply().content, "hello");
  assert.deepEqual(reply().blocks, [{ type: "text", text: "hello" }]);
  assert.equal(stamps.count, 1);
  runFrame();
  assert.equal(stamps.count, 1);
  stamps.restore();
});

test("a tool event flushes pending text before the card lands", () => {
  reset();
  const stamps = countReplyStamps();
  send({ type: "text", text: "hi" });
  assert.equal(reply()?.content ?? "", "");
  send({
    type: "tool_use",
    tool: "read",
    tool_call_id: "tc_1",
    input: "{}",
  });
  assert.equal(reply().content, "hi");
  assert.equal(reply().blocks[0].type, "text");
  assert.equal(reply().blocks[0].text, "hi");
  assert.equal(reply().blocks[1].type, "tool");
  assert.equal(reply().blocks[1].tool, "read");
  assert.equal(stamps.count, 2);
  runFrame();
  assert.equal(reply().content, "hi");
  assert.equal(reply().blocks.length, 2);
  assert.equal(stamps.count, 2);
  stamps.restore();
});

test("session_loaded discard does not stamp another session", () => {
  reset();
  send({ type: "text", text: "nope" });
  useSessionStore.setState({ currentSessionId: "other" });
  clearSessionByMsgId();
  runFrame();
  assert.equal(useSessionStore.getState().messagesById[RID]?.content ?? "", "");
  assert.equal(useSessionStore.getState().messagesById.other, undefined);
});
