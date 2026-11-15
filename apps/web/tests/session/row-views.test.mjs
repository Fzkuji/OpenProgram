// Rows live per view. A conversation and a sub-agent's transcript can hold the
// same id as two rows; numbered updates and job text frames reach only the
// views that hold the row.
import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import { installLoader } from "../_support/load.mjs";
import { installDom } from "../_support/dom.mjs";

installLoader();
installDom({ pathname: "/s/main" });
globalThis.WebSocket ??= { OPEN: 1 };
globalThis.requestAnimationFrame = undefined;
const { selectRow, selectViewRows, useSessionStore } = await import("../../lib/session-store/index.ts");
const { applyChatWsMessage } = await import("../../lib/net/ws/chat-stream.ts");
const { flushTextFrames } = await import("../../lib/net/ws/text-frames.ts");
const { subAgentChatKey } = await import("../../lib/chat/transcript/sub-agent-transcripts.ts");
const { transcriptRows } = await import("../_support/transcript-rows.mjs");

const MAIN = "main";
const SUB = subAgentChatKey(MAIN, "j_a");
const row = (id, content, fields = {}) => ({ id, role: "assistant", rowKind: "assistant", content, status: "done", timestamp: 1, ...fields });
const store = () => useSessionStore.getState();
const rows = transcriptRows(useSessionStore, selectRow);
const held = (view, id) => rows.readRow(view, id);

beforeEach(() => {
  useSessionStore.setState({ rowsByView: {}, overlaysByView: {}, messageOrder: {}, executionUpdateOrders: {} });
});
afterEach(() => { mock.restoreAll(); });

test("the same id in the main and sub-agent views stays two rows", () => {
  rows.putRow(MAIN, row("x", "main copy"));
  rows.putRow(SUB, row("x", "agent copy"));
  assert.equal(held(MAIN, "x").content, "main copy");
  assert.equal(held(SUB, "x").content, "agent copy");

  rows.patchRow(SUB, "x", { content: "agent edit" });
  assert.equal(held(MAIN, "x").content, "main copy");
  assert.equal(held(SUB, "x").content, "agent edit");
  rows.patchRow(MAIN, "x", { content: "main edit" });
  assert.equal(held(SUB, "x").content, "agent edit");
});

test("a read of one view leaves the other's row in place", () => {
  rows.putRows(SUB, [row("x", "agent copy"), row("y", "agent other")]);
  rows.putRow(MAIN, row("x", "main copy"));
  rows.replaceRows(MAIN, [row("z", "main moved on")]);
  assert.deepEqual(store().messageOrder[SUB], ["x", "y"]);
  assert.equal(held(SUB, "x").content, "agent copy");
  assert.equal(held(MAIN, "x"), undefined);
});

test("a local change naming a view that does not hold its row falls back to the one view that does", () => {
  rows.putRow(SUB, row("only", "agent"));
  store().applyLocalChange(MAIN, { kind: "local_patch", rowId: "only", fields: { content: "patched" } });
  assert.equal(held(SUB, "only").content, "patched");
  assert.equal(held(MAIN, "only"), undefined);
});

test("with more than one holder, a local change naming neither writes nothing and logs", () => {
  const error = mock.method(console, "error", () => {});
  rows.putRow(MAIN, row("x", "main"));
  rows.putRow(SUB, row("x", "agent"));
  store().applyLocalChange("elsewhere", { kind: "local_patch", rowId: "x", fields: { content: "lost" } });
  assert.equal(held(MAIN, "x").content, "main");
  assert.equal(held(SUB, "x").content, "agent");
  assert.equal(error.mock.callCount(), 1);
});

test("a local change for a row no view holds changes nothing", () => {
  store().applyLocalChange(MAIN, { kind: "local_patch", rowId: "ghost", fields: { content: "x" } });
  assert.equal(store().rowsByView[MAIN], undefined);
});

test("removing a conversation drops its views and keeps another conversation's rows", () => {
  store().upsertConversation({ id: MAIN, title: "x" });
  rows.putRow(MAIN, row("x", "main"));
  rows.putRow(SUB, row("j", "agent"));
  rows.putRow("other", row("x", "other copy"));
  store().removeConversation(MAIN);
  assert.equal(store().rowsByView[MAIN], undefined);
  assert.equal(store().rowsByView[SUB], undefined);
  assert.equal(held("other", "x").content, "other copy");
});

test("a selector for an empty view or id reads nothing and has a stable empty map", () => {
  assert.equal(selectRow(null, "x")(store()), undefined);
  assert.equal(selectRow(MAIN, undefined)(store()), undefined);
  assert.equal(selectViewRows("none")(store()), selectViewRows(null)(store()));
});

const jobText = (offset, text) => applyChatWsMessage({ type: "chat_response", data: {
  session_id: MAIN, msg_id: "j_a_user", job_id: "j_a", type: "stream_event", row_id: "j_a_user_reply",
  item_id: "j_a_user_reply_b0", item_index: 0, offset, event: { type: "text", text },
} });

test("job text reaches every view holding the reply, and its end writes no row", () => {
  rows.putRow(MAIN, row("j_a_user_reply", "", { status: "running", blocks: [] }));
  rows.putRow(SUB, row("j_a_user_reply", "", { status: "running", blocks: [] }));
  jobText(0, "agent ");
  jobText(6, "text");
  flushTextFrames();
  assert.equal(held(MAIN, "j_a_user_reply").content, "agent text");
  assert.equal(held(SUB, "j_a_user_reply").content, "agent text");
  useSessionStore.setState({ currentSessionId: MAIN });
  applyChatWsMessage({ type: "chat_response", data: { session_id: MAIN, msg_id: "j_a_user", job_id: "j_a", type: "result", content: "agent text" } });
  assert.equal(held(MAIN, "j_a_user_reply").status, "running");
  assert.equal(held(MAIN, "j_a_user_reply").autoPreviewFiles, undefined);
});

test("a conversation on another branch gets no row from a job", () => {
  rows.putRows(MAIN, [{ id: "j_a_user", role: "user", rowKind: "user", content: "on this branch", status: "done", timestamp: 1 }, row("other", "elsewhere")]);
  jobText(0, "agent text");
  flushTextFrames();
  applyChatWsMessage({ type: "chat_response", data: { session_id: MAIN, msg_id: "j_a_user", job_id: "j_a", type: "result", content: "agent text" } });
  assert.equal(held(MAIN, "j_a_user_reply"), undefined);
  assert.deepEqual(store().messageOrder[MAIN], ["j_a_user", "other"]);
  assert.equal(store().messageOrder[SUB], undefined);
});
