/**
 * Live GUI system-access markers must key on the persisted code-node id
 * (`tree.path`), not the live envelope `msg_id`. fn-form completion is a
 * `tree_update` plus `load_session` replacement; there is no
 * `display=runtime` result envelope.
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
globalThis.requestAnimationFrame = () => 1;
globalThis.cancelAnimationFrame = () => {};

const { useSessionStore } = await import("../lib/session-store/index.ts");
const { applyChatWsMessage } = await import("../lib/net/chat-stream.ts");
const { convToChatMsgs } = await import("../lib/conv-mapper.ts");
const {
  hasSystemAccessRequest,
  consumeSystemAccessRequest,
  systemAccessRequestId,
} = await import("../lib/system-access-result.ts");

const blocked = {
  status: "infeasible",
  reason_code: "system_access_required",
  system_access: [{ id: "screen_recording", status: "not_granted" }],
};

function tree(path, output = blocked) {
  return {
    path,
    name: "gui_agent",
    status: "completed",
    output: JSON.stringify(output),
  };
}

function reset(sid) {
  useSessionStore.setState({
    messagesById: {},
    messageOrder: {},
    currentSessionId: sid,
  });
}

test("fn-form tree_update marks the code-node id used after load_session", () => {
  const sid = "fnform_sess";
  const nodeId = "fnform_node";
  reset(sid);
  applyChatWsMessage({
    type: "chat_response",
    data: {
      type: "tree_update",
      session_id: sid,
      msg_id: nodeId,
      function: "gui_agent",
      tree: tree(nodeId),
    },
  });
  const persisted = convToChatMsgs([
    {
      id: nodeId,
      role: "assistant",
      type: "status",
      display: "runtime",
      function: "gui_agent",
      status: "done",
      context_tree: tree(nodeId),
    },
  ]);
  useSessionStore.getState().setMessages(sid, persisted);
  const row = useSessionStore.getState().messagesById[nodeId];
  assert.equal(row?.id, nodeId);
  assert.equal(hasSystemAccessRequest(sid, row.id), true);
});

test("LLM tree_update envelope id is the assistant; marker uses tree.path", () => {
  const sid = "llm_sess";
  const assistantId = "asst_live";
  const nodeId = "gui_node_live";
  reset(sid);
  applyChatWsMessage({
    type: "chat_response",
    data: {
      type: "tree_update",
      session_id: sid,
      msg_id: assistantId,
      function: "gui_agent",
      tree: tree(nodeId),
    },
  });
  assert.equal(hasSystemAccessRequest(sid, nodeId), true);
  assert.equal(hasSystemAccessRequest(sid, assistantId), false);
});

test("repeated live tree_update after consume does not remount the marker", () => {
  const sid = "dedupe_sess";
  const nodeId = "dedupe_node";
  reset(sid);
  const frame = {
    type: "chat_response",
    data: {
      type: "tree_update",
      session_id: sid,
      msg_id: nodeId,
      function: "gui_agent",
      tree: tree(nodeId),
    },
  };
  applyChatWsMessage(frame);
  consumeSystemAccessRequest(sid, nodeId);
  applyChatWsMessage(frame);
  assert.equal(hasSystemAccessRequest(sid, nodeId), false);
});

test("tree_update for a background session does not mark the focused session", () => {
  const focused = "focused_sess";
  const background = "bg_sess";
  const nodeId = "bg_node";
  reset(focused);
  applyChatWsMessage({
    type: "chat_response",
    data: {
      type: "tree_update",
      session_id: background,
      msg_id: nodeId,
      function: "gui_agent",
      tree: tree(nodeId),
    },
  });
  assert.equal(hasSystemAccessRequest(background, nodeId), false);
  assert.equal(hasSystemAccessRequest(focused, nodeId), false);
});

test("running tree_update without a blocked result does not mark", () => {
  const sid = "running_sess";
  const nodeId = "running_node";
  reset(sid);
  applyChatWsMessage({
    type: "chat_response",
    data: {
      type: "tree_update",
      session_id: sid,
      msg_id: nodeId,
      function: "gui_agent",
      tree: { path: nodeId, name: "gui_agent", status: "running", output: "" },
    },
  });
  assert.equal(hasSystemAccessRequest(sid, nodeId), false);
});

test("display=runtime result also marks context_tree.path, not envelope id", () => {
  const sid = "result_sess";
  const envelopeId = "result_envelope";
  const nodeId = "result_node";
  reset(sid);
  applyChatWsMessage({
    type: "chat_response",
    data: {
      type: "result",
      display: "runtime",
      session_id: sid,
      msg_id: envelopeId,
      function: "gui_agent",
      context_tree: tree(nodeId),
    },
  });
  const row = useSessionStore.getState().messagesById[envelopeId];
  assert.equal(row?.id, envelopeId);
  const accessId = systemAccessRequestId(row?.contextTree, row?.id || "");
  assert.equal(accessId, nodeId);
  assert.equal(hasSystemAccessRequest(sid, accessId), true);
  assert.equal(hasSystemAccessRequest(sid, row.id), false);
  consumeSystemAccessRequest(sid, accessId);
  assert.equal(hasSystemAccessRequest(sid, accessId), false);
});

test("historical conv mapper never marks a persisted gui_agent row", () => {
  const sid = "hist_sess";
  const nodeId = "hist_node";
  reset(sid);
  const msgs = convToChatMsgs([
    {
      id: nodeId,
      role: "assistant",
      type: "status",
      display: "runtime",
      function: "gui_agent",
      status: "done",
      context_tree: tree(nodeId),
    },
  ]);
  assert.equal(msgs[0]?.id, nodeId);
  assert.equal(hasSystemAccessRequest(sid, nodeId), false);
});
