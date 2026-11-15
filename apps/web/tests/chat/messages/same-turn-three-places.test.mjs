// One turn draws through one row list: the main chat, a split pane and a
// sub-agent's expanded view render the same rows from the same server rows.
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
// No garbage-collection timers, so the test process can exit.
const client = new QueryClient({ defaultOptions: { queries: { gcTime: Infinity, retry: false } } });
// Every place renders inside the app's query client, as the shell does.
const h = (type, props) => createElement(QueryClientProvider, { client }, createElement(type, props));
const { selectRow, useSessionStore } = await import("../../../lib/session-store/index.ts");
const { transcriptRows } = await import("../../_support/transcript-rows.mjs");
const rows = transcriptRows(useSessionStore, selectRow);
const { subAgentChatKey } = await import("../../../lib/chat/transcript/sub-agent-transcripts.ts");
const { MessageList } = await import("../../../components/chat/messages/list/message-list.tsx");
const { PeerSessionPane } = await import("../../../components/chat/pane/peer-session-pane.tsx");
const { SubAgentTranscript } = await import("../../../components/chat/messages/cards/sub-agent-transcript.tsx");

const SID = "same-turn";
const AGENT = "job-1";
// Rows as the server projection sends them (transcript/rows.py decides rowKind).
const turn = [
  { id: "u", role: "user", rowKind: "user", origin: "person", content: "[task from no-such-session] a typed line", status: "done", timestamp: 1 },
  { id: "r", role: "user", rowKind: "received_message", origin: "agent_delivery", content: "[message from sender-session (helper)]\nthe delivered body", status: "done", timestamp: 2 },
  { id: "a", role: "assistant", rowKind: "assistant", content: "the **reply**", status: "done", timestamp: 3,
    blocks: [{ type: "text", text: "the **reply**" }] },
];
const seed = () => {
  useSessionStore.setState({ currentSessionId: SID, activeChatKey: SID });
  rows.putRows(SID, structuredClone(turn));
  rows.putRows(subAgentChatKey(SID, AGENT), structuredClone(turn));
};

const rowHtml = (container, id) => container.querySelector(`[data-msg-id="${id}"]`)?.outerHTML ?? null;
const replyBody = (container) => container.querySelector('[data-msg-id="a"] .chat-stream-body')?.innerHTML ?? null;

test("the main chat and a split pane draw the same turn identically", async () => {
  seed();
  const main = await render(h(MessageList));
  const pane = await render(h(PeerSessionPane, { tabId: "t", sessionId: SID, title: "x" }));
  for (const { id } of turn) {
    const drawn = rowHtml(main.container, id);
    assert.ok(drawn, `the main chat draws ${id}`);
    assert.equal(rowHtml(pane.container, id), drawn, `${id} draws the same in the pane`);
  }
  await pane.unmount();
  await main.unmount();
});

test("the reply reads the same in all three places", async () => {
  seed();
  const main = await render(h(MessageList));
  const pane = await render(h(PeerSessionPane, { tabId: "t", sessionId: SID, title: "x" }));
  const card = { id: "card", role: "assistant", rowKind: "sub_agent_step", content: "", attach: { job_id: AGENT, status: "completed" } };
  const sub = await render(h(SubAgentTranscript, { card, sessionId: SID }));
  const body = replyBody(main.container);
  assert.ok(body?.includes("<strong>reply</strong>"), body);
  assert.equal(replyBody(pane.container), body);
  assert.equal(replyBody(sub.container), body);
  await sub.unmount();
  await pane.unmount();
  await main.unmount();
});

test("a typed [task from line stays the person's message and a delivered one shows as received", async () => {
  seed();
  const main = await render(h(MessageList));
  const typed = main.container.querySelector('.message.user[data-msg-id="u"]');
  assert.ok(typed, "the typed line is a user bubble");
  assert.ok(typed.textContent.includes("[task from no-such-session] a typed line"));
  const received = main.container.querySelector('[data-msg-id="r"]');
  assert.equal(main.container.querySelector('.message.user[data-msg-id="r"]'), null);
  assert.ok(received.textContent.includes("Message from “helper”"), received.textContent);
  await main.unmount();
});
