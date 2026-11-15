/**
 * The client's published-rows path over the real store, frame handlers and
 * transcript update reducer: numbered updates, text frames by offset, local
 * rows, overlays and the acks that carry a first read.
 */
import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import { installLoader } from "../../_support/load.mjs";
import { installDom } from "../../_support/dom.mjs";

installLoader({});
installDom({ pathname: "/s/S" });
globalThis.WebSocket = class { static OPEN = 1; };
globalThis.requestAnimationFrame = undefined;

const { selectRow, useSessionStore } = await import("../../../lib/session-store/index.ts");
const { applyChatWsMessage, appendLocalUserTurn } = await import("../../../lib/net/ws/chat-stream.ts");
const { flushTextFrames } = await import("../../../lib/net/ws/text-frames.ts");
const { applyTranscriptUpdate, applyFirstRead } = await import("../../../lib/runtime-bridge/session/transcript-updates.ts");
const { handleExecutionCommandUpdated, handleChatResponse, wsHandleChatAck } = await import("../../../lib/runtime-bridge/session/chat-handlers.ts");
const { applyReadToView } = await import("../../../lib/chat/transcript/apply-read.ts");
const { stopOverlay } = await import("../../../lib/chat/transcript/apply-transcript-update.ts");
const { useSessionHistory } = await import("../../../lib/chat/history/session-history.ts");
const { setPendingUserText, clearPendingUserText } = await import("../../../lib/chat/send/pending-user-text.ts");
const { useSendQueue } = await import("../../../lib/chat/send/send-queue.ts");
const { settleSendsOnDisconnect } = await import("../../../lib/net/ws/unacked-sends.ts");
const { runtimeState } = await import("../../../lib/runtime-bridge/state.ts");

const SID = "S";
const store = () => useSessionStore.getState();
const row = (id, view = SID) => selectRow(view, id)(store());
const order = (view = SID) => store().messageOrder[view] ?? [];
const window0 = { head_id: "u0", revision: 10, before: null, after: null, start: 0, total: 1 };
const user0 = { id: "u0", role: "user", content: "first", status: "done", timestamp: 1 };
let revision;

function update(changes) {
  applyTranscriptUpdate({ session_id: SID, view: SID, previous: revision, revision: revision + 1, changes });
  revision += 1;
}

function textFrame(rowId, offset, text, extra = {}) {
  applyChatWsMessage({ type: "chat_response", data: {
    type: "stream_event", session_id: SID, msg_id: "u1", row_id: rowId, item_id: "text:0", item_index: 0,
    offset, event: { type: "text", text }, ...extra,
  } });
  flushTextFrames();
}

function pendingSend(id, requestId) {
  setPendingUserText(SID, "hello", 5, { messageId: id, requestId });
  appendLocalUserTurn(SID, id, "hello", undefined, 5, "pending", undefined, requestId);
}

beforeEach(() => {
  revision = 10;
  clearPendingUserText(SID);
  store().clearConversations?.();
  runtimeState.currentSessionId = SID;
  useSessionHistory.setState({ pages: {} });
  applyFirstRead(SID, { turns: [{ id: "u0", rows: [user0] }], window: window0 });
});

test("a first read seeds the window and moves the cursor", () => {
  assert.deepEqual(order(), ["u0"]);
  assert.equal(useSessionHistory.getState().pages[SID].revision, 10);
});

test("an ack carrying a first read replaces the pending row by request id", () => {
  pendingSend("local1", "R1");
  wsHandleChatAck({ session_id: SID, msg_id: "u1", first_read: {
    turns: [{ id: "u1", rows: [{ id: "u1", role: "user", content: "hello", status: "done", timestamp: 6, clientRequestId: "R1" }] }],
    window: { ...window0, revision: 20, head_id: "u1" },
  } });
  assert.deepEqual(order(), ["u1"]);
  assert.equal(useSessionHistory.getState().pages[SID].revision, 20);
});

test("a turn_upsert that beats the ack replaces the local row, and the ack finds it done", () => {
  pendingSend("local1", "R1");
  const turn = { id: "u1", rows: [
    { id: "u1", role: "user", content: "hello", status: "done", timestamp: 6, clientRequestId: "R1" },
    { id: "u1_reply", role: "assistant", content: "", status: "running", timestamp: 7 },
  ] };
  update([{ kind: "turn_upsert", turn, afterTurnId: "u0" }]);
  assert.deepEqual(order(), ["u0", "u1", "u1_reply"]);
  applyChatWsMessage({ type: "chat_ack", data: { session_id: SID, msg_id: "u1", client_request_id: "R1" } });
  assert.deepEqual(order(), ["u0", "u1", "u1_reply"]);
  assert.equal(row("u1").status, "done");
});

test("an ack with published rows re-ids the local row and mints no reply; the turn then fills in", () => {
  pendingSend("local1", "R1");
  applyChatWsMessage({ type: "chat_ack", data: { session_id: SID, msg_id: "u1", client_request_id: "R1" } });
  assert.deepEqual(order(), ["u0", "u1"]);
  update([{ kind: "turn_upsert", afterTurnId: "u0", turn: { id: "u1", rows: [
    { id: "u1", role: "user", content: "hello", status: "done", timestamp: 6 },
    { id: "u1_reply", role: "assistant", content: "", status: "running", timestamp: 7 },
  ] } }]);
  assert.deepEqual(order(), ["u0", "u1", "u1_reply"]);
});

test("the ack of a /goal message turns its bubble into the labelled objective", () => {
  setPendingUserText(SID, "/goal ship it", 5, { messageId: "local1", requestId: "R1" });
  appendLocalUserTurn(SID, "local1", "/goal ship it", undefined, 5, "pending", undefined, "R1");
  applyChatWsMessage({ type: "chat_ack", data: {
    session_id: SID, msg_id: "u1", client_request_id: "R1", text: "ship it", starts_goal: true,
  } });
  assert.equal(row("u1").content, "ship it");
  assert.equal(row("u1").startsGoal, true);
});

test("a /goal sent with no bubble gets its labelled one from the ack", () => {
  setPendingUserText(SID, "/goal ship it", 5, { requestId: "R2" });
  applyChatWsMessage({ type: "chat_ack", data: {
    session_id: SID, msg_id: "u2", client_request_id: "R2", text: "ship it", starts_goal: true,
  } });
  assert.deepEqual(order(), ["u0", "u2"]);
  assert.equal(row("u2").content, "ship it");
  assert.equal(row("u2").startsGoal, true);
});

test("a reconnect after a lost ack leaves one user row", () => {
  pendingSend("local1", "R1");
  clearPendingUserText(SID);
  settleSendsOnDisconnect();
  assert.equal(row("local1").errorReason, "disconnected");
  applyReadToView(SID, [user0, { id: "u1", role: "user", content: "hello", status: "done", timestamp: 6, clientRequestId: "R1" }]);
  assert.deepEqual(order(), ["u0", "u1"]);
});

test("a new turn is drawn before a function placeholder at the tail", () => {
  store().applyLocalChange(SID, { kind: "local_append", row: { id: "ph", role: "assistant", rowKind: "function_run", content: "", status: "running", clientRequestId: "F1" } });
  update([{ kind: "turn_upsert", afterTurnId: "u0", turn: { id: "peer", rows: [{ id: "peer", role: "user", content: "x", status: "done", timestamp: 9 }] } }]);
  assert.deepEqual(order(), ["u0", "peer", "ph"]);
  applyReadToView(SID, [user0, { id: "fn", role: "assistant", content: "", status: "running", timestamp: 8, clientRequestId: "F1" }]);
  assert.deepEqual(order(), ["u0", "fn"]);
});

test("a notice survives a read after its row, and leaves with that row", () => {
  store().applyLocalChange(SID, { kind: "local_append", afterRowId: "u0", row: { id: "n", role: "system", rowKind: "system_text", content: "note", status: "done" } });
  const later = { id: "u2", role: "user", content: "y", status: "done", timestamp: 9 };
  applyReadToView(SID, [user0, later]);
  assert.deepEqual(order(), ["u0", "n", "u2"]);
  applyReadToView(SID, [later]);
  assert.deepEqual(order(), ["u2"]);
});

test("a read or update naming a steer's command settles it", () => {
  const queue = { id: "q", text: "s", steerCommand: { command_id: "CMD" }, createdAt: 0 };
  useSendQueue.setState({ queues: { [SID]: [queue] } });
  applyReadToView(SID, [user0, { id: "st", role: "user", content: "s", status: "done", timestamp: 9, clientRequestId: "CMD" }]);
  assert.deepEqual(useSendQueue.getState().queues[SID] ?? [], []);
  useSendQueue.setState({ queues: { [SID]: [{ ...queue, steerCommand: { command_id: "CMD2" } }] } });
  update([{ kind: "turn_upsert", afterTurnId: "u0", turn: { id: "st2", rows: [{ id: "st2", role: "user", content: "s", status: "done", timestamp: 9, clientRequestId: "CMD2" }] } }]);
  assert.deepEqual(useSendQueue.getState().queues[SID] ?? [], []);
});

function openReply() {
  update([{ kind: "turn_upsert", afterTurnId: "u0", turn: { id: "u1", rows: [
    { id: "u1", role: "user", content: "hi", status: "done", timestamp: 6 },
    { id: "u1_reply", role: "assistant", content: "", status: "streaming", blocks: [], timestamp: 7 },
  ] } }]);
}

test("published text lands by offset, twice changes nothing, and a lost frame asks for one read", () => {
  openReply();
  textFrame("u1_reply", 0, "he");
  textFrame("u1_reply", 2, "llo");
  textFrame("u1_reply", 2, "llo");
  assert.equal(row("u1_reply").content, "hello");
  const sent = [];
  runtimeState.ws = { readyState: 1, send: (raw) => sent.push(JSON.parse(raw)), addEventListener() {}, removeEventListener() {} };
  textFrame("u1_reply", 40, "late");
  textFrame("u1_reply", 44, "later");
  runtimeState.ws = null;
  assert.equal(row("u1_reply").content, "hello");
  assert.equal(sent.filter((frame) => frame.action === "load_session").length, 1);
});

test("a text frame naming no held row creates nothing", () => {
  textFrame("ghost_reply", 0, "x");
  assert.deepEqual(order(), ["u0"]);
});

test("citations land on the block their text_end closes without touching its text", () => {
  openReply();
  textFrame("u1_reply", 0, "cited");
  textFrame("u1_reply", 0, "", { event: { type: "text_end", citations: [{ url: "https://a.example", title: "A" }] }, offset: undefined });
  assert.equal(row("u1_reply").content, "cited");
  assert.equal(row("u1_reply").blocks[0].citations.length, 1);
});

test("callRoots and runtimeChildren update from numbered changes", () => {
  openReply();
  update([{ kind: "item_upsert", turnId: "u1", rowId: "u1_reply", list: "callRoots", item: { path: "p1", name: "f", status: "running" } }]);
  update([{ kind: "item_upsert", turnId: "u1", rowId: "u1_reply", list: "callRoots", item: { path: "p1", name: "f", status: "completed" } }]);
  update([{ kind: "item_upsert", turnId: "u1", rowId: "u1_reply", list: "runtimeChildren", item: { id: "c", status: "running" } }]);
  assert.deepEqual(row("u1_reply").callRoots, [{ path: "p1", name: "f", status: "completed" }]);
  assert.equal(row("u1_reply").runtimeChildren[0].id, "c");
});

test("a published terminal frame writes no row", () => {
  openReply();
  applyChatWsMessage({ type: "chat_response", data: { type: "result", session_id: SID, msg_id: "u1", content: "x" } });
  assert.equal(row("u1_reply").status, "streaming");
  handleChatResponse({ type: "result", session_id: SID, msg_id: "u1", content: "x" });
  assert.equal(row("u1_reply").content, "");
});

test("a rejected Stop clears its overlay, the row returns to running and later text appears", () => {
  openReply();
  update([{ kind: "row_patch", turnId: "u1", rowId: "u1_reply", fields: { status: "running" } }]);
  store().setRunningTaskFor(SID, { session_id: SID, msg_id: "u1", execution_id: "e1", status_version: 1 }, "never");
  store().applyLocalChange(SID, stopOverlay("u1_reply", [{ until_command_rejected: "C1" }]));
  assert.equal(row("u1_reply").status, "cancelled");
  assert.equal(row("u1_reply").status, "cancelled");
  textFrame("u1_reply", 0, "dropped");
  assert.equal(row("u1_reply").content, "");
  runtimeState._optimisticCancels.C1 = { sessionId: SID, task: { session_id: SID, msg_id: "u1", execution_id: "e1" }, messageId: "u1_reply" };
  handleExecutionCommandUpdated({ command: { command_id: "C1", execution_id: "e1", status: "rejected", latest_snapshot: { execution_id: "e1", session_id: SID, status: "running", status_version: 2 } } });
  assert.equal(row("u1_reply").status, "running");
  textFrame("u1_reply", 0, "back");
  assert.equal(row("u1_reply").content, "back");
});

test("a Stop overlay ends on the terminal row and a later cancelling cannot pull it back", () => {
  openReply();
  store().applyLocalChange(SID, stopOverlay("u1_reply"));
  update([{ kind: "row_patch", turnId: "u1", rowId: "u1_reply", fields: { status: "cancelling" } }]);
  assert.equal(row("u1_reply").status, "cancelled");
  update([{ kind: "row_upsert", turnId: "u1", afterRowId: "u1", row: { id: "u1_reply", role: "assistant", content: "kept", status: "cancelled", timestamp: 7 } }]);
  assert.equal(store().overlaysByView[SID], undefined);
  assert.equal(row("u1_reply").content, "kept");
});

test("an until_read overlay leaves when a whole read lands, not on one page", () => {
  store().applyLocalChange(SID, { kind: "overlay_set", rowId: "u0", fields: { siblingIndex: 3 }, clear: ["until_read"] });
  applyReadToView(SID, [user0], false);
  assert.equal(row("u0").siblingIndex, 3);
  applyReadToView(SID, [user0]);
  assert.equal(row("u0").siblingIndex, undefined);
});

test("a drawn row keeps one object until something changes", () => {
  store().applyLocalChange(SID, { kind: "overlay_set", rowId: "u0", fields: { siblingIndex: 3 }, clear: ["until_read"] });
  assert.equal(row("u0"), row("u0"));
  assert.equal(store().rowsByView[SID].u0.siblingIndex, undefined);
});

test("a send refused before its ack shows why on its own row and mints no reply", () => {
  pendingSend("local1", "R1");
  applyChatWsMessage({ type: "chat_response", data: { type: "error", session_id: SID, msg_id: "fresh", content: "Model is disabled", client_request_id: "R1" } });
  assert.equal(row("local1").status, "error");
  assert.equal(row("local1").errorDetail, "Model is disabled");
  assert.equal(row("fresh_reply"), undefined);
  assert.deepEqual(order(), ["u0", "local1"]);
});

test("a local row without a server copy keeps its kind and anchor", () => {
  pendingSend("local1", "R1");
  assert.equal(row("local1").rowKind, "user");
  assert.equal(row("local1").localAnchor, "tail");
  assert.equal(row("local1").clientRequestId, "R1");
});

for (const [name, ending, rows] of [
  ["a no-op", { type: "compaction_finished", no_op: true }, 1],
  // Nothing changed and a later request tries again: nothing to say.
  ["a failure", { type: "compaction_failed", error: "timeout" }, 0],
]) {
  test(`a turn's own compaction ending in ${name} names the step only while it lasts`, () => {
    store().setRunningTaskFor(SID, { msg_id: "m1", execution_id: "e1" }, "never");
    handleChatResponse({ session_id: SID, type: "compaction_started" });
    // The run indicator names the step; no transcript row is added for it.
    assert.equal(store().runningTasks[SID].phase, "compact");
    assert.equal(order().filter((id) => id.startsWith("compaction_")).length, 0);
    handleChatResponse({ session_id: SID, ...ending });
    assert.equal(store().runningTasks[SID].phase, undefined);
    assert.equal(order().filter((id) => id.startsWith("compaction_")).length, rows);
    store().setRunningTaskFor(SID, null, "never");
  });
}

test("a /compact the person stopped says stopped, and a failed one says why", () => {
  handleChatResponse({
    session_id: SID, type: "compaction_failed", user_initiated: true, cancelled: true,
    error: "Compaction cancelled; history unchanged",
  });
  const stopped = order().filter((id) => id.startsWith("compaction_failed_"));
  assert.equal(stopped.length, 1);
  assert.match(row(stopped[0]).content, /stopped/i);
  assert.doesNotMatch(row(stopped[0]).content, /failed/i);
});
