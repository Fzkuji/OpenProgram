// A New chat's first message shows the moment it is sent. Until chat_ack the
// draft has no conversation id (currentSessionId is null) and its rows live
// under the draft's own key; a slow acknowledgement once left the page blank
// for the whole wait while the composer already showed the running turn.
import assert from "node:assert/strict";
import { test } from "node:test";
import { installLoader } from "../../../_support/load.mjs";
import { installDom, render } from "../../../_support/dom.mjs";

installLoader({
  replace: {
    "/components/chat/composer/index.tsx": "export const Composer = () => null;",
  },
});
installDom({ pathname: "/chat" });
const { createElement } = await import("react");
const { QueryClient, QueryClientProvider } = await import("@tanstack/react-query");
const client = new QueryClient({ defaultOptions: { queries: { gcTime: Infinity, retry: false } } });
const h = (type, props) => createElement(QueryClientProvider, { client }, createElement(type, props));
const { useSessionStore } = await import("../../../../lib/session-store/index.ts");
const { appendLocalUserTurn } = await import("../../../../lib/net/ws/chat-stream.ts");
const { applyTranscriptChanges } = await import("../../../../lib/chat/transcript/apply-transcript-update.ts");
const { MessageList, transcriptViewKey } = await import("../../../../components/chat/messages/list/message-list.tsx");

const DRAFT = "local_6a29be7a-2e93-42f1-97fb-6f2e10a51063";

function sendFirstMessage() {
  // What sendChatMessage leaves behind once the socket took the frame.
  useSessionStore.getState().setCurrentDraft(DRAFT);
  appendLocalUserTurn(DRAFT, "pending_1", "Reply with just the word OK.", undefined, Date.now(), "pending");
  useSessionStore.getState().setRunningTaskFor(DRAFT, { session_id: DRAFT, msg_id: "", started_at: Date.now() / 1000 });
}

test("the view key is the conversation, else the draft's own key", () => {
  assert.equal(transcriptViewKey("local_abc", "local_abc"), "local_abc");
  assert.equal(transcriptViewKey(null, DRAFT), DRAFT);
  assert.equal(transcriptViewKey(null, "__new__"), null);
  assert.equal(transcriptViewKey(null, null), null);
});

test("a New chat draws its sent message and the pending reply before chat_ack", async () => {
  sendFirstMessage();
  assert.equal(useSessionStore.getState().currentSessionId, null, "no conversation id before the ack");
  const list = await render(h(MessageList));
  const bubble = list.container.querySelector('[data-msg-id="pending_1"]');
  assert.ok(bubble, "the sent message is on the page");
  assert.ok(bubble.textContent.includes("Reply with just the word OK."));
  assert.ok(list.container.querySelector(".pending-standalone"),
    "the waiting reply is shown under it");
  await list.unmount();
});

test("the acknowledged conversation keeps drawing the same message", async () => {
  sendFirstMessage();
  // chat_ack: the draft key becomes the conversation's id.
  useSessionStore.getState().setCurrentConv(DRAFT);
  const list = await render(h(MessageList));
  assert.ok(list.container.querySelector('[data-msg-id="pending_1"]'), "the message stays after the ack");
  await list.unmount();
});

// The bubble a send draws is one DOM node from Enter to the reply: the
// acknowledgement renames its row and must not mount it again (the appear
// animation would start over) or change its height (the row would jump).
const SENT = "local_sent_once";
function sendInto(sessionId, requestId) {
  useSessionStore.getState().setCurrentConv(sessionId);
  appendLocalUserTurn(sessionId, `pending_${requestId}`, "hello", undefined, Date.now(), "pending", undefined, requestId);
}
const footerOf = (bubble) => bubble.querySelector(".message-actions-footer");

test("the acknowledgement keeps the sent bubble mounted and as tall as it was", async () => {
  sendInto(SENT, "req-1");
  const list = await render(h(MessageList));
  const before = list.container.querySelector('[data-msg-id="pending_req-1"]');
  assert.ok(before.classList.contains("is-pending"));
  assert.ok(footerOf(before), "the action row holds its place before the acknowledgement");
  await list.act(() => {
    const store = useSessionStore.getState();
    const local = store.rowsByView[SENT]["pending_req-1"];
    store.applyLocalChange(SENT, {
      kind: "local_replace", rowId: "pending_req-1",
      row: { ...local, id: "server-1", status: "done", localAnchor: undefined },
    });
  });
  const after = list.container.querySelector('[data-msg-id="server-1"]');
  assert.equal(after, before, "the same node, renamed");
  assert.equal(after.classList.contains("is-pending"), false);
  assert.equal(footerOf(after), footerOf(before));
  assert.equal(list.container.querySelector('[data-msg-id="pending_req-1"]'), null);
  await list.unmount();
});

test("the server's row arriving before the acknowledgement keeps the bubble mounted too", async () => {
  const sessionId = "local_server_first";
  sendInto(sessionId, "req-2");
  const list = await render(h(MessageList));
  const before = list.container.querySelector('[data-msg-id="pending_req-2"]');
  await list.act(() => {
    const store = useSessionStore.getState();
    const view = { order: store.messageOrder[sessionId], rows: store.rowsByView[sessionId] };
    const row = { id: "server-2", role: "user", rowKind: "user", content: "hello", status: "done", clientRequestId: "req-2" };
    store.writeView(sessionId, applyTranscriptChanges(view, [
      { kind: "turn_upsert", turn: { id: "server-2", rows: [row] }, afterTurnId: null },
    ], { holdsTurn: () => false, atLatest: true }));
  });
  assert.equal(list.container.querySelector('[data-msg-id="server-2"]'), before);
  await list.unmount();
});

test("the waiting indicator stands in the box of the empty reply that replaces it", async () => {
  const sessionId = "local_waiting_box";
  sendInto(sessionId, "req-3");
  useSessionStore.getState().setRunningTaskFor(sessionId, { session_id: sessionId, msg_id: "", started_at: Date.now() / 1000 });
  const list = await render(h(MessageList));
  const shape = (message) => [...message.children].map((child) => child.className.split(" ").find((name) =>
    name === "message-actions-footer" || name === "pending-body" || name === "chat-stream-body"));
  const waiting = list.container.querySelector(".pending-standalone");
  assert.deepEqual(shape(waiting), ["pending-body", "message-actions-footer"]);
  assert.equal(waiting.querySelector(".message-actions-footer").getAttribute("aria-hidden"), "true");
  await list.act(() => {
    useSessionStore.getState().applyLocalChange(sessionId, {
      kind: "local_append",
      row: { id: "reply-3", role: "assistant", rowKind: "assistant", content: "", status: "streaming", timestamp: Date.now() },
    });
  });
  assert.equal(list.container.querySelector(".pending-standalone"), null);
  const reply = list.container.querySelector('[data-msg-id="reply-3"]');
  assert.deepEqual(shape(reply), ["chat-stream-body", "message-actions-footer"]);
  assert.ok(reply.querySelector(".chat-stream-body > .pending-body"));
  await list.unmount();
  useSessionStore.getState().setRunningTaskFor(sessionId, null);
});

test.after(() => {
  // No turn is left running, so no status timer keeps the process alive.
  useSessionStore.getState().setRunningTaskFor(DRAFT, null);
  client.clear();
});
