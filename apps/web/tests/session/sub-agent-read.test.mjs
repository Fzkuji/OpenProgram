// A sub-agent's transcript is read in whole turns under its own view key.
import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { installLoader } from "../_support/load.mjs";
import { installDom } from "../_support/dom.mjs";

installLoader();
installDom({ pathname: "/s/main" });
globalThis.WebSocket ??= { OPEN: 1 };
const { selectRow, useSessionStore } = await import("../../lib/session-store/index.ts");
const { loadSubAgentTranscript, subAgentChatKey } = await import("../../lib/chat/transcript/sub-agent-transcripts.ts");
const { setSocket } = await import("../../lib/runtime-bridge/state.ts");
const { transcriptRows } = await import("../_support/transcript-rows.mjs");
const rows = transcriptRows(useSessionStore, selectRow);

const MAIN = "main";
const SUB = subAgentChatKey(MAIN, "j_a");
const row = (id, content, fields = {}) => ({ id, role: "assistant", rowKind: "assistant", content, status: "done", timestamp: 1, ...fields });
const store = () => useSessionStore.getState();
const held = (view, id) => selectRow(view, id)(store());

beforeEach(() => {
  useSessionStore.setState({ rowsByView: {}, overlaysByView: {}, messageOrder: {}, executionUpdateOrders: {} });
});
afterEach(() => setSocket(null));

class Socket {
  readyState = 1;
  sent = [];
  listeners = new Set();
  addEventListener(type, listener) { if (type === "message") this.listeners.add(listener); }
  removeEventListener(type, listener) { if (type === "message") this.listeners.delete(listener); }
  send(payload) { this.sent.push(JSON.parse(payload)); }
  reply(data) {
    const event = { data: JSON.stringify({ type: "sub_agent_transcript", data }) };
    for (const listener of [...this.listeners]) listener(event);
  }
}

async function read(turns, window = { head_id: "tip", revision: 1, before: null, after: null, start: 0, end: turns.length, total: turns.length }) {
  const socket = new Socket();
  setSocket(socket);
  const pending = loadSubAgentTranscript(MAIN, "j_a", { jobId: "j_a" });
  await Promise.resolve();
  socket.reply({ request_id: socket.sent[0].request_id, action: "load_sub_agent_transcript", session_id: MAIN, job_id: "j_a", view: SUB, head_id: "tip", turns, window });
  return { tip: await pending, sent: socket.sent[0] };
}

test("an agent's read fills its view from whole turns and leaves the main view alone", async () => {
  rows.putRows(MAIN, [row("j_a_user_reply", "main copy")]);
  const { tip, sent } = await read([{ id: "j_a_user", rows: [
    { id: "j_a_user", role: "user", content: "the task", status: "done", timestamp: 1 },
    row("j_a_user_reply", "agent reply", { tools: [{ id: "c1", tool: "read" }],
      blocks: [{ type: "tool", tool: "read", tool_call_id: "c1", input: "{}", result: "seen" }] }),
  ] }]);
  assert.equal(sent.action, "load_sub_agent_transcript");
  assert.equal(tip, "tip");
  assert.deepEqual(store().messageOrder[SUB], ["j_a_user", "j_a_user_reply"]);
  assert.equal(held(SUB, "j_a_user_reply").content, "agent reply");
  assert.equal(held(SUB, "j_a_user_reply").tools[0].result, "seen", "the read's tool body reaches the row");
  assert.equal(held(MAIN, "j_a_user_reply").content, "main copy");
});

test("a turn that started after the server read the branch stays at the end of the view", async () => {
  rows.putRows(MAIN, [row("late", "the main view's row")]);
  rows.putRows(SUB, [row("late", "the agent's newer row", { status: "streaming" })]);
  await read([{ id: "j_a_user", rows: [{ id: "j_a_user", role: "user", content: "the task", status: "done", timestamp: 1 }] }]);
  assert.deepEqual(store().messageOrder[SUB], ["j_a_user", "late"]);
  assert.equal(held(SUB, "late").content, "the agent's newer row");
});

test("an empty or failed read leaves the view as it was", async () => {
  rows.putRows(SUB, [row("kept", "kept")]);
  const { tip } = await read([], null);
  assert.equal(tip, null);
  assert.deepEqual(store().messageOrder[SUB], ["kept"]);
});
