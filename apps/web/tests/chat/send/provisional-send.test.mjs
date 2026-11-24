/**
 * Provisional sends: a chat turn from a draft (local_* key) or a session,
 * from the moment the socket accepts it until the backend's chat_ack.
 *
 * Drives the real send-chat-message, pending-user-text, chat-stream,
 * chat-handlers and session store over a fake socket. The tests share one
 * store and run in order.
 */
import assert from "node:assert/strict";
import { after, describe, test } from "node:test";

import { installLoader } from "../../_support/load.mjs";
import { installDom, render } from "../../_support/dom.mjs";

installLoader({
  transpileTs: true,
  // A React dialog lib modules only reach through confirmAction.
  stubs: {
    "@/components/ui/confirm-dialog": "export const confirmAction = async () => true;"
      + " export const ConfirmDialog = () => null; export const ConfirmDialogHost = () => null;",
  },
});
installDom({ pathname: "/chat" });
// The app's socket, as the WebSocket hook opens it (see "socket routing").
class FakeWebSocket {
  static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
  static instances = [];
  readyState = 0;
  frames = [];
  constructor(url) { this.url = url; FakeWebSocket.instances.push(this); }
  send(raw) { this.frames.push(JSON.parse(raw)); }
  close() { this.readyState = 3; this.onclose?.({}); }
  addEventListener() {}
  removeEventListener() {}
  dispatchEvent() { return true; }
  receive(frame) { this.onmessage?.({ data: JSON.stringify(frame) }); }
}
globalThis.WebSocket = FakeWebSocket;
globalThis.fetch = async () => new Response(null, { status: 204 });

const sent = [];
const { runtimeState, setSocket } = await import("../../../lib/runtime-bridge/state.ts");
const openSocket = () => setSocket({ readyState: 1, send: (payload) => sent.push(JSON.parse(payload)) });
openSocket();
runtimeState.currentSessionId = null;

const { sendChatMessage } = await import("../../../components/chat/composer/submit/send-chat-message.ts");
const pendingUserText = await import("../../../lib/chat/send/pending-user-text.ts");
const { selectRow, useSessionStore } = await import("../../../lib/session-store/index.ts");
const { transcriptRows } = await import("../../_support/transcript-rows.mjs");
const { applyChatWsMessage } = await import("../../../lib/net/ws/chat-stream.ts");
const { releaseChatOperationError } = await import("../../../lib/net/ws/use-ws.ts");
const { useSendQueue, releaseStopHold } = await import("../../../lib/chat/send/send-queue.ts");
const chatHandlers = await import("../../../lib/runtime-bridge/session/chat-handlers.ts");
const channelDrafts = await import("../../../lib/runtime-bridge/actions/draft-channel-choice.ts");
const target = await import("../../../components/chat/composer/modes/fn-form/session-target.ts");
const attachmentDb = await import("../../../components/chat/composer/attach/attach-idb.ts");
const { stopSession } = await import("../../../components/chat/composer/submit/use-chat-submit.ts");

const store = () => useSessionStore.getState();
const transcript = transcriptRows(useSessionStore, selectRow);
/** The row with this id in whichever view holds it. */
const message = (id) => {
  for (const view of Object.keys(store().rowsByView)) {
    const row = selectRow(view, id)(store());
    if (row) return row;
  }
  return undefined;
};
const provisional = "local_duplicate_send";
const send = (text) => sendChatMessage({ text, sessionId: provisional, thinking: "medium", webSearchEnabled: false });
const forget = (sid) => {
  pendingUserText.clearPendingUserText(sid);
  pendingUserText.clearPendingFirstAck(sid);
};
const quietly = (fn) => {
  const original = console.error;
  const logged = [];
  console.error = (...args) => logged.push(args);
  try { return [fn(), logged]; } finally { console.error = original; }
};

describe("first ACK of a draft", () => {
  test("a duplicate submit before the first ACK is handled locally and keeps the first text", () => {
    assert.equal(send("first"), true);
    assert.equal(send("second"), true, "a duplicate UI submit is handled locally");
    assert.equal(sent.length, 1, "only one provisional turn may wait for its first ACK");
    assert.equal(pendingUserText.getPendingUserText(provisional), "first");
    assert.ok(store().runningTasks[provisional], "the provisional chat key becomes running immediately");
    assert.ok(Number.isFinite(pendingUserText.getPendingUserTimestamp(provisional)),
      "a successful send captures the user-message timestamp immediately");
  });

  test("an operation_error for a chat releases the send lock without running ACK cleanup", () => {
    const sid = "local_operation-error-chat";
    let cleanups = 0;
    assert.equal(sendChatMessage({
      text: "retry after operation error", sessionId: sid, thinking: "medium", webSearchEnabled: false,
      hasAttachments: true, onAck: () => { cleanups += 1; },
    }), true);
    assert.ok(store().runningTasks[sid]);
    assert.equal(releaseChatOperationError({ action: "chat", session_id: sid, code: "handler_error" }), true);
    assert.equal(cleanups, 0, "operation_error cannot run ACK cleanup");
    assert.equal(pendingUserText.hasPendingFirstAck(sid), false);
    assert.equal(store().runningTasks[sid], undefined);
    assert.equal(sendChatMessage({
      text: "retry after operation error", sessionId: sid, thinking: "medium", webSearchEnabled: false, hasAttachments: true,
    }), true, "the rejected chat draft can be sent again");
    forget(sid);
  });

  test("the live user bubble carries the original send time", () => {
    const sentAt = pendingUserText.getPendingUserTimestamp(provisional);
    applyChatWsMessage({ type: "chat_ack", data: { session_id: provisional, msg_id: "sent-user-1", text: "first" } });
    assert.equal(message("sent-user-1").timestamp, sentAt);
  });
});

describe("ACK boundary", () => {
  test("a resized image keeps its original bytes, and cleanup runs once at chat_ack, not on socket write", () => {
    const sid = "local_ack-boundary";
    let cleanups = 0;
    const image = {
      type: "image", data: "aW1hZ2U=", media_type: "image/png",
      original_data: "b3JpZ2luYWw=", original_media_type: "image/jpeg",
    };
    assert.equal(sendChatMessage({
      text: "caption with image", sessionId: sid, attachments: [image],
      thinking: "medium", webSearchEnabled: false, onAck: () => { cleanups += 1; },
    }), true);
    assert.deepEqual(sent.at(-1).attachments[0], image);
    assert.equal(cleanups, 0, "socket write success cannot run ACK cleanup");
    assert.equal(pendingUserText.pendingUserHasAttachments(sid), true);
    applyChatWsMessage({ type: "chat_ack", data: { session_id: sid, msg_id: "accepted-attachment", text: "caption with image" } });
    assert.equal(cleanups, 1, "backend chat_ack runs the captured cleanup once");
    forget(sid);
  });

  test("a definitive rejection keeps the draft but releases the duplicate-submit lock", () => {
    const sid = "local_rejected-attachment";
    let cleanups = 0;
    assert.equal(sendChatMessage({
      text: "caption with rejected image", sessionId: sid,
      attachments: [{ type: "image", data: "aW1hZ2U=", media_type: "image/png" }],
      thinking: "medium", webSearchEnabled: false, hasAttachments: true, onAck: () => { cleanups += 1; },
    }), true);
    applyChatWsMessage({
      type: "chat_response",
      data: { type: "error", session_id: sid, msg_id: "rejected-attachment", reason: "rejected" },
    });
    assert.equal(cleanups, 0);
    assert.equal(pendingUserText.hasPendingFirstAck(sid), false);
    forget(sid);
  });

  test("run_active is not an ACK and never requeues an attachment turn as plain text", async () => {
    const sid = "local_run-active-attachment";
    let cleanups = 0;
    // A native path-only document has no inline payload but is still an attachment turn.
    assert.equal(sendChatMessage({
      text: "retry this image", sessionId: sid, thinking: "medium", webSearchEnabled: false,
      hasAttachments: true, onAck: () => { cleanups += 1; },
    }), true);
    applyChatWsMessage({
      type: "chat_response",
      data: { type: "error", session_id: sid, msg_id: "run-active-attachment", code: "run_active", retry_query: "retry this image" },
    });
    await Promise.resolve();
    assert.equal(cleanups, 0);
    assert.equal(useSendQueue.getState().queues[sid], undefined);
    // Later tests count from the first provisional send.
    sent.length = 1;
  });
});

describe("message timestamps", () => {
  test("every live message appended to the store gets a timestamp", () => {
    transcript.localRow("timestamp-system", { id: "system-without-explicit-time", role: "system", rowKind: "system_event", content: "transient notice", status: "done" });
    assert.ok(Number.isFinite(message("system-without-explicit-time").timestamp));
  });

  test("history keeps persisted times and gives legacy rows the load time, not their index", () => {
    const persisted = 1_700_000_000;
    const loadTime = 1_700_000_009_000;
    const realNow = Date.now;
    Date.now = () => loadTime;
    try {
      transcript.replaceRows("timestamp-history", [
        { id: "history-authoritative-time", role: "assistant", content: "persisted", status: "done", timestamp: persisted },
        { id: "history-missing-time", role: "user", content: "legacy", status: "done" },
        { id: "history-second-missing-time", role: "assistant", content: "legacy reply", status: "done" },
      ]);
    } finally {
      Date.now = realNow;
    }
    assert.equal(message("history-authoritative-time").timestamp, persisted);
    assert.equal(message("history-missing-time").timestamp, loadTime);
    assert.equal(message("history-second-missing-time").timestamp, loadTime);
  });

  test("mid-run hydration prefers a persisted time, else keeps the live start time", () => {
    const persisted = 1_700_000_000;
    transcript.putRow("timestamp-hydrate", { id: "live-placeholder", role: "assistant", content: "", status: "streaming", timestamp: 1_111 });
    transcript.replaceRows("timestamp-hydrate", [
      { id: "live-placeholder", role: "assistant", content: "", status: "running", timestamp: persisted },
    ]);
    assert.equal(message("live-placeholder").timestamp, persisted);

    transcript.putRow("timestamp-hydrate-missing", {
      id: "live-placeholder-without-persisted-time", role: "assistant", content: "", status: "streaming", timestamp: 2_222,
    });
    transcript.replaceRows("timestamp-hydrate-missing", [
      { id: "unrelated-before-placeholder", role: "user", content: "older", status: "done" },
      { id: "live-placeholder-without-persisted-time", role: "assistant", content: "", status: "running" },
    ]);
    assert.equal(message("live-placeholder-without-persisted-time").timestamp, 2_222);
  });

  test("nested runtime and attach rows inherit their parent's time", () => {
    const persisted = 1_700_000_000;
    transcript.replaceRows("timestamp-nested", [{
      id: "parent-with-nested-rows", role: "assistant", content: "parent", status: "done", timestamp: persisted,
      runtimeChildren: [{ id: "nested-runtime", role: "assistant", content: "", status: "running" }],
      attachCards: [{ id: "nested-attach", role: "assistant", content: "", status: "running" }],
    }]);
    const parent = message("parent-with-nested-rows");
    assert.equal(parent.runtimeChildren[0].timestamp, persisted);
    assert.equal(parent.attachCards[0].timestamp, persisted);
  });

});

describe("failed and local sends", () => {
  test("a local command response releases the reservation so the draft can send again", () => {
    store().setCurrentDraft(provisional);
    chatHandlers.handleChatResponse({ type: "local_command", session_id: provisional, content: "local output" });
    assert.equal(pendingUserText.hasPendingFirstAck(provisional), false);
    assert.equal(pendingUserText.getPendingUserText(provisional), undefined);
    assert.equal(store().runningTasks[provisional], undefined);
    assert.equal(runtimeState.isRunning, false);
    assert.equal(send("after local command"), true);
    assert.equal(sent.filter((frame) => frame.action === "chat").length, 2, "a local command response allows the next send");
    chatHandlers.handleChatResponse({ type: "local_command", session_id: provisional, content: "second local output" });
  });

  test("a command that only answers leaves its reply line in the draft and no user bubble", () => {
    const sid = "local_reply_only";
    store().setCurrentDraft(sid);
    assert.equal(sendChatMessage({ text: "/goal status", sessionId: sid, thinking: "medium", webSearchEnabled: false, bubble: false }), true);
    const users = () => (store().messageOrder[sid] ?? []).map(message).filter((row) => row?.role === "user");
    assert.deepEqual(users(), [], "no bubble while the server decides");
    chatHandlers.handleChatResponse({ type: "local_command", session_id: sid, content: "No goal set." });
    assert.deepEqual(users(), []);
    const rows = (store().messageOrder[sid] ?? []).map(message);
    assert.deepEqual(rows.map((row) => [row.rowKind, row.content]), [["system_text", "No goal set."]]);
    assert.equal(pendingUserText.hasPendingFirstAck(sid), false);
  });

  test("a bubble drawn for a command that turned out to only answer is removed", () => {
    const sid = "local_reply_typed";
    store().setCurrentDraft(sid);
    sendChatMessage({ text: "/goal status", sessionId: sid, thinking: "medium", webSearchEnabled: false });
    assert.equal((store().messageOrder[sid] ?? []).length, 1);
    chatHandlers.handleChatResponse({ type: "local_command", session_id: sid, content: "No goal set." });
    assert.deepEqual((store().messageOrder[sid] ?? []).map(message).map((row) => row.role), ["system"]);
  });

  test("a throwing socket write returns false, logs the error and releases the provisional turn", () => {
    const sid = "local_send-throws";
    store().setRunningTaskFor(sid, { session_id: sid, msg_id: "optimistic", started_at: Date.now() / 1000 });
    const failure = new Error("socket closed during send");
    setSocket({ readyState: 1, send: () => { throw failure; } });
    const [result, logged] = quietly(() => sendChatMessage({ text: "retry me", sessionId: sid, thinking: "medium", webSearchEnabled: false }));
    assert.equal(result, false, "send failure is reported through false, not thrown");
    assert.equal(pendingUserText.hasPendingFirstAck(sid), false);
    assert.equal(pendingUserText.getPendingUserText(sid), undefined);
    assert.equal(store().runningTasks[sid], undefined);
    assert.equal(logged.at(-1)?.at(-1), failure, "the caught exception stays observable");
  });

  test("a failed send restores the previous pending text and timestamp", () => {
    const sid = "existing-pending-send";
    pendingUserText.setPendingUserText(sid, "older pending", 777);
    setSocket({ readyState: 1, send: () => { throw new Error("closed"); } });
    const [result] = quietly(() => sendChatMessage({ text: "new send that fails", sessionId: sid, thinking: "medium", webSearchEnabled: false }));
    assert.equal(result, false);
    assert.equal(pendingUserText.getPendingUserText(sid), "older pending");
    assert.equal(pendingUserText.getPendingUserTimestamp(sid), 777);
    pendingUserText.clearPendingUserText(sid);
  });

  test("a socket that closes during the write counts as not sent", () => {
    const sid = "local_send-closes";
    store().setRunningTaskFor(sid, { session_id: sid, msg_id: "optimistic", started_at: Date.now() / 1000 });
    setSocket({ readyState: 1, send() { this.readyState = 3; } });
    assert.equal(sendChatMessage({ text: "retry after close", sessionId: sid, thinking: "medium", webSearchEnabled: false }), false);
    assert.equal(pendingUserText.hasPendingFirstAck(sid), false);
    assert.equal(pendingUserText.getPendingUserText(sid), undefined);
    assert.equal(store().runningTasks[sid], undefined);
    openSocket();
  });

  test("the user timestamp and the optimistic run share the time the socket accepted the send", () => {
    const sid = "timestamp-send-success";
    const realNow = Date.now;
    let clock = 1_000;
    Date.now = () => clock;
    setSocket({ readyState: 1, send: (payload) => { sent.push(JSON.parse(payload)); clock = 5_000; } });
    try {
      assert.equal(sendChatMessage({ text: "accepted now", sessionId: sid, thinking: "medium", webSearchEnabled: false }), true);
    } finally {
      Date.now = realNow;
    }
    assert.equal(pendingUserText.getPendingUserTimestamp(sid), 5_000);
    assert.equal(store().runningTasks[sid].started_at, 5);
    pendingUserText.clearPendingUserText(sid);
    store().setRunningTaskFor(sid, null);
    openSocket();
  });
});

describe("channel choice", () => {
  test("a draft send keeps its captured channel after another real session activates", () => {
    const sid = "local_channel-owner";
    runtimeState.currentSessionId = "real-session-b";
    channelDrafts.draftChannelChoiceHost.__pendingChannelChoices = { [sid]: { channel: "wechat", account_id: "work" } };
    assert.equal(sendChatMessage({ text: "channel owner", sessionId: sid, thinking: "medium", webSearchEnabled: false }), true);
    assert.equal(sent.at(-1).channel, "wechat");
    assert.equal(sent.at(-1).account_id, "work");
  });

  test("ACK cleanup clears the keyed choice and only the compatibility choice the draft owns", () => {
    const acknowledged = {};
    channelDrafts.setDraftChannelChoice(acknowledged, "local_channel-owner", { channel: "wechat", account_id: "work" });
    channelDrafts.dropDraftChannelChoice(acknowledged, "local_channel-owner");
    assert.equal(channelDrafts.draftChannelChoiceFor(acknowledged, "local_channel-owner"), null);
    assert.equal(acknowledged._pendingChannelChoice, null);

    const legacy = { _pendingChannelChoice: { channel: "slack", account_id: "team" } };
    channelDrafts.dropDraftChannelChoice(legacy, "local_channel-owner", true);
    assert.equal(legacy._pendingChannelChoice, null, "an active legacy first-turn ACK clears the unkeyed choice");

    const background = {};
    channelDrafts.setDraftChannelChoice(background, "local_background-a", { channel: "wechat", account_id: "a" });
    channelDrafts.switchDraftChannelChoice(background, "local_background-a", "local_active-b");
    channelDrafts.setDraftChannelChoice(background, "local_active-b", { channel: "slack", account_id: "b" });
    channelDrafts.dropDraftChannelChoice(background, "local_background-a", false);
    assert.deepEqual(background._pendingChannelChoice, { channel: "slack", account_id: "b" },
      "a background ACK does not clear another draft's global choice");

    const other = {};
    channelDrafts.setDraftChannelChoice(other, "local_ack-owner", { channel: "wechat", account_id: "owner" });
    other._pendingChannelChoice = { channel: "slack", account_id: "other" };
    channelDrafts.dropDraftChannelChoice(other, "local_ack-owner", true);
    assert.deepEqual(other._pendingChannelChoice, { channel: "slack", account_id: "other" },
      "an active ACK does not clear a compatibility choice owned by another draft");
  });

  test("chat_ack consumes the channel choice its first payload carried", () => {
    const sid = "local_ack-consumes-channel";
    channelDrafts.setDraftChannelChoice(channelDrafts.draftChannelChoiceHost, sid, { channel: "wechat", account_id: "w" });
    chatHandlers.wsHandleChatAck({ session_id: sid, msg_id: "ack-channel" });
    assert.equal(channelDrafts.draftChannelChoiceFor(channelDrafts.draftChannelChoiceHost, sid), null);
    store().setRunningTaskFor(sid, null, "always");
  });
});

describe("function-form target", () => {
  test("a function form targets the draft key, or the real session", () => {
    assert.equal(target.resolveFnFormSessionId(null, provisional), provisional);
    assert.equal(target.resolveFnFormSessionId("real", "real"), "real");
  });

  test("a failed dispatch clears the legacy running flag only when it still owns the visible chat", () => {
    assert.equal(target.shouldClearLegacyRunning(provisional, "local_other", null), false);
    assert.equal(target.shouldClearLegacyRunning("real-a", "real-b", "real-b"), false);
    assert.equal(target.shouldClearLegacyRunning(provisional, provisional, null), true);
  });
});

describe("closed draft attachments", () => {
  test("closing a draft notifies the live cache and blocks a late IndexedDB write", async () => {
    let notified = null;
    const unsubscribe = attachmentDb.onAttachmentOwnerClosed((owner) => { notified = owner; });
    attachmentDb.markAttachmentOwnerClosed("local_closed-attachments");
    unsubscribe();
    assert.equal(notified, "local_closed-attachments");
    assert.equal(attachmentDb.attachmentOwnerIsClosed("local_closed-attachments"), true);
    let opens = 0;
    window.indexedDB = { open: () => { opens++; throw new Error("closed owner must not open IndexedDB"); } };
    await attachmentDb.saveAttachments("local_closed-attachments", { images: [], docs: [] });
    assert.equal(opens, 0, "a FileReader completion after draft close does not persist its owner");
  });
});

describe("per-session composer settings", () => {
  test("patching a background session's settings never touches the focused one", () => {
    store().setCurrentDraft("focused_chat");
    store().setComposerSettings({ thinking: "high" });
    store().setComposerSettings({ webSearch: false }, "background_chat");
    const settings = store().composerSettingsBySession;
    assert.equal(settings.focused_chat.thinking, "high");
    assert.equal(settings.focused_chat.webSearch, true);
    assert.equal(settings.background_chat.thinking, "", "a background session does not inherit the focused settings");
  });

  test("a send carries the draft's extra working directories only when they were set", () => {
    const frames = [];
    setSocket({ readyState: 1, send: (payload) => frames.push(JSON.parse(payload)) });
    for (const [suffix, dirs] of [["empty", []], ["set", ["/extra"]], ["unset", undefined]]) {
      const key = `local_project_directories_${suffix}`;
      store().setCurrentDraft(key);
      if (dirs !== undefined) store().setAdditionalWorkingDirs(key, dirs);
      assert.equal(sendChatMessage({ text: "test directory intent", sessionId: key, thinking: "medium", webSearchEnabled: false }), true);
      const frame = frames.at(-1);
      assert.equal(frame.session_id, key);
      if (dirs === undefined) assert.equal(Object.hasOwn(frame, "additional_working_dirs"), false);
      else assert.deepEqual(frame.additional_working_dirs, dirs);
      forget(key);
      store().setRunningTaskFor(key, null, "always");
    }
    openSocket();
  });
});

describe("Stop and execution commands", () => {
  const sid = "stop-session";
  // A Stop holds the queue and asks a server that is not there; let go of it.
  after(() => {
    releaseStopHold(sid, false);
    releaseStopHold("local_stop-before-ack", false);
  });
  const reset = () => {
    store().setRunningTaskFor(sid, null, "always");
    runtimeState._optimisticCancels = {};
    runtimeState._optimisticStops = {};
  };

  test("Stop marks the live reply cancelled and frees the slot, without a cancelling flag", () => {
    reset();
    transcript.putRow(sid, { id: "exec-1", role: "assistant", rowKind: "assistant", content: "partial", status: "running" });
    store().setRunningTaskFor(sid, { session_id: sid, msg_id: "u1", execution_id: "exec-1", status_version: 3 });
    const frames = [];
    stopSession(sid, (frame) => { frames.push(frame); return true; });
    assert.deepEqual(
      { ...frames[0], command_id: typeof frames[0].command_id },
      { type: "execution.command", action: "execution.cancel", command_id: "string", execution_id: "exec-1", expected_version: 3, payload: {} },
    );
    assert.equal(message("exec-1").status, "cancelled");
    assert.equal(store().runningTasks[sid], undefined);
  });

  test("a Stop goes out once, and an accepted answer leaves the reply stopping until the turn ends", () => {
    reset();
    transcript.putRow(sid, { id: "exec-4", role: "assistant", rowKind: "assistant", content: "partial", status: "running" });
    store().setRunningTaskFor(sid, { session_id: sid, msg_id: "u4", execution_id: "exec-4", status_version: 1 });
    const frames = [];
    stopSession(sid, (frame) => { frames.push(frame); return true; });
    chatHandlers.handleExecutionCommandUpdated({ command: {
      command_id: frames[0].command_id, execution_id: "exec-4", status: "accepted", result_version: 4,
    } });
    assert.equal(frames.length, 1);
    assert.equal(message("exec-4").status, "cancelled", "the stop is under way");
    assert.equal(store().runningTasks[sid], undefined);
    assert.ok(runtimeState._optimisticCancels[frames[0].command_id], "closed by the execution's cancelled frame");
  });

  test("a refused stop restores the task from the snapshot, only when it owns the foreground", () => {
    for (const [status, owns] of [["running", true], ["paused", false]]) {
      reset();
      store().setRunningTaskFor(sid, { session_id: sid, msg_id: "u1", execution_id: "exec-1", status_version: 3 });
      const frames = [];
      stopSession(sid, (frame) => { frames.push(frame); return true; });
      chatHandlers.handleExecutionCommandUpdated({ command: {
        command_id: frames[0].command_id, execution_id: "exec-1", status: "rejected", rejection_code: "unsupported_capability",
        latest_snapshot: { execution_id: "exec-1", session_id: sid, status, status_version: 4 },
      } });
      assert.equal(frames.length, 1, "only a stale refusal is sent again");
      const task = store().runningTasks[sid];
      if (owns) {
        assert.equal(task.execution_id, "exec-1");
        assert.equal(task.status_version, 4);
        assert.equal(task.cancelling, false);
      } else {
        assert.equal(task, undefined, "a paused execution does not take the composer back");
      }
      assert.deepEqual(runtimeState._optimisticCancels, {});
    }
  });

  test("a refused stop whose turn the slot already holds at a newer version takes the stop off the reply", () => {
    reset();
    transcript.putRow(sid, { id: "exec-3", role: "assistant", rowKind: "assistant", content: "partial", status: "running" });
    store().setRunningTaskFor(sid, { session_id: sid, msg_id: "u3", execution_id: "exec-3", status_version: 3 });
    const frames = [];
    stopSession(sid, (frame) => { frames.push(frame); return true; });
    assert.equal(message("exec-3").status, "cancelled");
    store().setRunningTaskFor(sid, { session_id: sid, msg_id: "u3", execution_id: "exec-3", status_version: 6 });
    chatHandlers.handleExecutionCommandUpdated({ command: {
      command_id: frames[0].command_id, execution_id: "exec-3", status: "rejected", rejection_code: "invalid_state",
      latest_snapshot: { execution_id: "exec-3", session_id: sid, status: "running", status_version: 5 },
    } });
    assert.equal(message("exec-3").status, "running");
    assert.equal(store().runningTasks[sid].status_version, 6);
    assert.deepEqual(runtimeState._optimisticCancels, {});
  });

  test("an applied command keeps the canonical running-task version", () => {
    reset();
    store().setRunningTaskFor(sid, { session_id: sid, msg_id: "u1", execution_id: "exec-2", status_version: 5 });
    chatHandlers.handleExecutionCommandUpdated({ command: {
      command_id: "steer-1", execution_id: "exec-2", status: "applied", result_version: 6,
    } });
    assert.equal(store().runningTasks[sid].status_version, 6);
  });

  test("an ACK that follows a pre-ACK Stop issues the exact cancel once the turn is identified", () => {
    reset();
    const draft = "local_stop-before-ack";
    store().setRunningTaskFor(draft, { session_id: draft, msg_id: "" });
    stopSession(draft, () => assert.fail("no cancel without an execution id"));
    assert.ok(runtimeState._optimisticStops[draft], "the Stop is remembered for the ACK");
    const before = sent.length;
    chatHandlers.wsHandleChatAck({ session_id: draft, msg_id: "u9", execution_id: "exec-9", status_version: 2 });
    const cancel = sent.slice(before).find((frame) => frame.action === "execution.cancel");
    assert.ok(cancel);
    assert.equal(cancel.execution_id, "exec-9");
    assert.equal(cancel.expected_version, 2);
    assert.equal(runtimeState._optimisticStops[draft], undefined);
  });

  test("a pre-ACK Stop goes out once when the ACK names the turn, whatever version it names", () => {
    reset();
    const draft = "local_stop-before-ack";
    store().setRunningTaskFor(draft, { session_id: draft, msg_id: "" });
    stopSession(draft, () => assert.fail("no cancel without an execution id"));
    const before = sent.length;
    chatHandlers.wsHandleChatAck({ session_id: draft, msg_id: "u10", execution_id: "exec-10", status_version: 1 });
    const first = sent.slice(before).find((frame) => frame.action === "execution.cancel");
    assert.equal(first.expected_version, 1);
    chatHandlers.handleExecutionCommandUpdated({ command: {
      command_id: first.command_id, execution_id: "exec-10", status: "accepted", result_version: 3,
    } });
    const cancels = sent.slice(before).filter((frame) => frame.action === "execution.cancel");
    assert.equal(cancels.length, 1);
    assert.equal(store().runningTasks[draft], undefined, "the stop is under way");
  });

  test("an ACK naming the turn but not its version sends the pre-ACK Stop at once", () => {
    reset();
    const draft = "local_stop-before-fn-ack";
    transcript.putRow(draft, { id: "exec-11", role: "assistant", rowKind: "assistant", content: "", status: "running" });
    store().setRunningTaskFor(draft, { session_id: draft, msg_id: "" });
    stopSession(draft, () => assert.fail("no cancel without an execution id"));
    assert.equal(message("exec-11").status, "cancelled");
    const before = sent.length;
    chatHandlers.wsHandleChatAck({ session_id: draft, msg_id: "u11", execution_id: "exec-11", function_run: true });
    try {
      const cancels = sent.slice(before).filter((frame) => frame.action === "execution.cancel");
      assert.equal(cancels.length, 1);
      assert.equal(cancels[0].execution_id, "exec-11");
      assert.equal(sent.slice(before).some((frame) => frame.action === "get_run_state"), false);
      assert.equal(store().runningTasks[draft], undefined, "the stop is under way");
    } finally {
      releaseStopHold(draft, false);
      store().setRunningTaskFor(draft, null, "always");
    }
  });
});

describe("theme diagnostics", () => {
  test("a send records only appearance metadata, and a failing logger never blocks it", () => {
    const events = [];
    document.documentElement.setAttribute("data-theme", "dark");
    window.openprogramDesktop = { theme: { trace: (event) => events.push(event) } };
    localStorage.setItem("agentic_theme_mode", "dark");
    setSocket({ readyState: 1, send() {} });
    const diagnosticSend = () => sendChatMessage({
      text: "PRIVATE_MESSAGE_SENTINEL", sessionId: "theme-diagnostic", thinking: "medium", webSearchEnabled: false, background: true,
    });
    try {
      assert.equal(diagnosticSend(), true);
      assert.equal(events.at(-1).source, "message-send");
      assert.equal(events.at(-1).theme, "dark");
      assert.equal(localStorage.getItem("agentic_theme_mode"), "dark");
      assert.doesNotMatch(JSON.stringify(events), /PRIVATE_MESSAGE_SENTINEL|theme-diagnostic/);
      window.openprogramDesktop.theme.trace = () => { throw new Error("diagnostic unavailable"); };
      assert.equal(diagnosticSend(), true);
    } finally {
      delete window.openprogramDesktop;
      // The two sends hold theme-diagnostic's run slot without an ack; left
      // in place, its run-state recheck lands in a later test's socket.
      forget("theme-diagnostic");
      store().setRunningTaskFor("theme-diagnostic", null, "always");
      openSocket();
    }
  });
});


describe("turn results", () => {
  test("a result that says the turn continues keeps the running task; a final result clears it", () => {
    const sid = "result-session";
    const task = { session_id: sid, msg_id: "turn-user", execution_id: "turn-exec", status_version: 1 };
    store().setRunningTaskFor(sid, task);
    chatHandlers.handleChatResponse({
      type: "result", session_id: sid, msg_id: "turn-user", execution_id: "turn-exec", content: "part one", turn_continues: true,
    });
    assert.equal(store().runningTasks[sid]?.execution_id, "turn-exec");
    chatHandlers.handleChatResponse({
      type: "result", session_id: sid, msg_id: "turn-user", execution_id: "turn-exec", content: "done",
    });
    assert.equal(store().runningTasks[sid], undefined);
  });
});

describe("composer socket writes", () => {
  test("the composer's socket write reports false instead of throwing when the socket fails", async () => {
    const { wsSend } = await import("../../../components/chat/composer/index.tsx");
    const failure = new Error("socket broke");
    setSocket({ readyState: 1, send: () => { throw failure; } });
    try {
      const [result, logged] = quietly(() => wsSend("raw frame"));
      assert.equal(result, false);
      assert.equal(logged.at(-1)?.at(-1), failure);
      setSocket({ readyState: 0, send: () => assert.fail("a closed socket is not written") });
      assert.equal(wsSend("raw frame"), false);
    } finally {
      openSocket();
    }
    assert.equal(wsSend('{"action":"ping"}'), true);
    assert.equal(sent.at(-1).action, "ping");
  });
});

describe("composer submit", () => {
  const mountSubmit = async (overrides) => {
    const { createElement } = await import("react");
    const { useChatSubmit } = await import("../../../components/chat/composer/submit/use-chat-submit.ts");
    let hook;
    function Probe() {
      hook = useChatSubmit({
        bound: null, input: "", activeChatKey: null, currentSessionId: null, isRunning: false,
        noEnabledModels: false, promptNeedModel() {}, send: () => true,
        setComposerInputFor: (owner, value) => store().setComposerInputFor(owner, value),
        setHistoryIndex() {}, slash: { runCommand: () => false, close() {} },
        pendingImages: [], pendingDocs: [], clearAttachmentsAfterSubmit() {},
        detachAttachmentsForSubmit: () => ({ release() {}, restore() {} }),
        thinking: "medium", webSearchEnabled: false, fastEnabled: false, fastSupported: false,
        dispatchFunction: () => false,
        ...overrides,
      });
      return null;
    }
    const view = await render(createElement(Probe));
    return { hook: () => hook, view };
  };

  test("a path-only native document still makes the turn an attachment turn", async () => {
    const sid = "local_path-only-doc";
    store().setCurrentDraft(sid);
    const doc = { id: "doc-1", filename: "notes.pdf", ext: "pdf", sizeBytes: 2048, sourcePath: "/Users/test/notes.pdf",
      content: null, dataB64: null, order: 0 };
    const { hook, view } = await mountSubmit({ activeChatKey: sid, input: "read this", pendingDocs: [doc] });
    try {
      await view.act(() => hook().submit());
      const frame = sent.at(-1);
      assert.equal(frame.session_id, sid);
      assert.equal(frame.attachments, undefined, "a native path travels as a marker, not inline bytes");
      assert.match(frame.text, /@json "\/Users\/test\/notes.pdf"/);
      assert.equal(pendingUserText.pendingUserHasAttachments(sid), true);
    } finally {
      await view.unmount();
      forget(sid);
      store().setRunningTaskFor(sid, null, "always");
    }
  });

  test("Stop targets the focused draft key when there is no real session yet", async () => {
    const sid = "local_stop-target";
    store().setRunningTaskFor(sid, { session_id: sid, msg_id: "u", execution_id: "draft-exec", status_version: 1 });
    const frames = [];
    const { hook, view } = await mountSubmit({
      activeChatKey: sid, currentSessionId: null, isRunning: true,
      send: (frame) => { frames.push(frame); return true; },
    });
    try {
      await view.act(() => hook().stop());
      assert.equal(frames[0].execution_id, "draft-exec");
      assert.equal(store().runningTasks[sid], undefined);
    } finally {
      await view.unmount();
      releaseStopHold(sid, false);
    }
  });
});

// The WebSocket hook routes frames to the handlers tested above. Runs last:
// mounting it replaces the shared socket with the hook's own.
describe("socket routing", () => {
  let view;
  let socket;
  const flush = async () => { for (let i = 0; i < 10; i++) await new Promise((resolve) => setTimeout(resolve, 0)); };
  after(async () => {
    // Idle every session first: an id-less running slot keeps asking a
    // socket for its run state once the hook has let go of it.
    for (const sid of Object.keys(store().runningTasks)) store().setRunningTaskFor(sid, null, "always");
    await view?.unmount();
  });

  test("the WebSocket hook opens the shared socket", async () => {
    const { createElement } = await import("react");
    const { useWS } = await import("../../../lib/net/ws/use-ws.ts");
    function Probe() { useWS(); return null; }
    view = await render(createElement(Probe));
    await flush();
    socket = FakeWebSocket.instances.at(-1);
    assert.ok(socket, "useWS opened a socket");
    socket.readyState = 1;
    socket.onopen?.();
    await flush();
  });

  test("an operation_error frame for a chat releases that pending send", () => {
    const sid = "local_routed-operation-error";
    assert.equal(sendChatMessage({ text: "rejected", sessionId: sid, thinking: "medium", webSearchEnabled: false }), true);
    assert.equal(socket.frames.at(-1).session_id, sid, "the send went over the hook's socket");
    socket.receive({ type: "operation_error", data: { action: "chat", session_id: sid, code: "handler_error" } });
    assert.equal(pendingUserText.hasPendingFirstAck(sid), false);
    assert.equal(store().runningTasks[sid], undefined);
  });

  test("chat_ack builds the user row before the bookkeeping clears the send time", () => {
    const sid = "local_routed-ack";
    const realNow = Date.now;
    let clock = 1_000_000;
    Date.now = () => clock;
    try {
      assert.equal(sendChatMessage({ text: "timed", sessionId: sid, thinking: "medium", webSearchEnabled: false }), true);
      clock = 9_000_000;
      socket.receive({ type: "chat_ack", data: { session_id: sid, msg_id: "routed-user", text: "timed" } });
    } finally {
      Date.now = realNow;
    }
    assert.equal(message("routed-user").timestamp, 1_000_000);
    assert.equal(pendingUserText.getPendingUserText(sid), undefined, "the ACK bookkeeping ran too");
    store().setRunningTaskFor(sid, null, "always");
  });

  test("execution.command.updated reads the command at the envelope root, never a nested payload", () => {
    const sid = "routed-command";
    store().setRunningTaskFor(sid, { session_id: sid, msg_id: "u", execution_id: "routed-exec", status_version: 1 });
    socket.receive({ type: "execution.command.updated", data: { command: {
      command_id: "c-legacy", execution_id: "routed-exec", status: "applied", result_version: 5,
    } } });
    assert.equal(store().runningTasks[sid].status_version, 1, "the removed nested payload is ignored");
    socket.receive({ type: "execution.command.updated", command: {
      command_id: "c-root", execution_id: "routed-exec", status: "applied", result_version: 2,
    } });
    assert.equal(store().runningTasks[sid].status_version, 2);
    store().setRunningTaskFor(sid, null, "always");
  });

  test("a terminal execution update for the shown session finishes the running UI", async () => {
    const sid = "routed-terminal";
    runtimeState.currentSessionId = sid;
    store().setRunningTaskFor(sid, { session_id: sid, msg_id: "u", execution_id: "terminal-exec", status_version: 1 });
    runtimeState.isRunning = true;
    socket.receive({ type: "execution.updated", execution: {
      execution_id: "terminal-exec", session_id: sid, status: "completed", status_version: 2, event_sequence: 1,
    } });
    await flush();
    assert.equal(store().runningTasks[sid], undefined);
    assert.equal(runtimeState.isRunning, false);
  });
});
