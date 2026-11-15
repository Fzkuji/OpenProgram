// A New chat's first message shows the moment it is sent. Until chat_ack the
// draft has no conversation id (currentSessionId is null) and its rows live
// under the draft's own key; a slow acknowledgement once left the page blank
// for the whole wait while the composer already showed the running turn.
import assert from "node:assert/strict";
import { test } from "node:test";
import { installLoader } from "../../_support/load.mjs";
import { installDom, render } from "../../_support/dom.mjs";

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
const { useSessionStore } = await import("../../../lib/session-store/index.ts");
const { appendLocalUserTurn } = await import("../../../lib/net/ws/chat-stream.ts");
const { MessageList, transcriptViewKey } = await import("../../../components/chat/messages/list/message-list.tsx");

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

test.after(() => {
  // No turn is left running, so no status timer keeps the process alive.
  useSessionStore.getState().setRunningTaskFor(DRAFT, null);
  client.clear();
});
