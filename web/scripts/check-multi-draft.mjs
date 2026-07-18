import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

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

const tabsModule = await import("../lib/state/center-tabs-store.ts");
const sessionModule = await import("../lib/session-store/index.ts");
const sendModule = await import("../components/chat/composer/legacy-send.ts");
const { useCenterTabs, sessionTabId, sessionAckIsActive } = tabsModule;
const { useSessionStore } = sessionModule;

useCenterTabs.setState({
  tabs: [{ id: "ntp:first", kind: "ntp", title: "" }],
  activeId: "ntp:first",
});

const first = useCenterTabs.getState().claimDraftSessionTab();
assert.match(first, /^local_[a-f0-9-]+$/, "first draft gets a provisional id");

useCenterTabs.getState().openNewTabPage();
const second = useCenterTabs.getState().claimDraftSessionTab();
assert.match(second, /^local_[a-f0-9-]+$/, "second draft gets a provisional id");
assert.notEqual(first, second, "each New chat is distinct");
assert.deepEqual(
  useCenterTabs.getState().tabs
    .filter((tab) => tab.kind === "session" && tab.draft)
    .map((tab) => tab.sessionId),
  [first, second],
);

const sessions = useSessionStore.getState();
assert.equal(typeof sessions.setCurrentDraft, "function");
sessions.setCurrentDraft(first);
useSessionStore.getState().setComposerInput("draft one");
useSessionStore.getState().setCurrentDraft(second);
assert.equal(useSessionStore.getState().composerInput, "");
useSessionStore.getState().setComposerInput("draft two");
useSessionStore.getState().setCurrentDraft(first);
assert.equal(useSessionStore.getState().composerInput, "draft one");

useCenterTabs.getState().setActive(sessionTabId(second));
useCenterTabs.getState().markSessionReady(first);
assert.equal(
  useCenterTabs.getState().activeId,
  sessionTabId(second),
  "a background acknowledgement does not steal focus",
);
assert.equal(
  useCenterTabs.getState().tabs.find((tab) => tab.sessionId === first)?.draft,
  false,
);
useCenterTabs.getState().closeTab(sessionTabId(first));
assert.equal(
  sessionAckIsActive(first),
  false,
  "an acknowledgement must not reopen a closed provisional tab",
);
assert.equal(useCenterTabs.getState().activeId, sessionTabId(second));

const sent = [];
globalThis.WebSocket = { OPEN: 1 };
Object.assign(globalThis.window, {
  ws: { readyState: 1, send: (payload) => sent.push(JSON.parse(payload)) },
  currentSessionId: null,
});

for (const [sessionId, text] of [[first, "one"], [second, "two"]]) {
  assert.equal(sendModule.sendChatMessage({
    text,
    sessionId,
    thinking: "medium",
    toolsEnabled: true,
    webSearchEnabled: false,
  }), true);
}
assert.deepEqual(sent.map((payload) => payload.session_id), [first, second]);
assert.deepEqual(globalThis.window.__pendingUserTextBySession, {
  [first]: "one",
  [second]: "two",
});

// Legacy response bookkeeping must route by the envelope's session rather
// than whichever tab happens to be visible when a background run completes.
const responseRouting = await import(
  "../lib/runtime-bridge/chat-response-routing.ts"
);
assert.equal(
  responseRouting.responseSessionId({ session_id: first }, second),
  first,
  "a background result belongs to its envelope session",
);
assert.equal(
  responseRouting.responseTargetsActiveChat({ session_id: first }, second),
  false,
  "a background result must not change active-chat controls",
);
assert.equal(
  responseRouting.responseTargetsActiveChat({}, second),
  true,
  "legacy unscoped responses still target the active chat",
);

const channelDrafts = await import(
  "../lib/runtime-bridge/draft-channel-choice.ts"
);
const channelHost = {};
channelDrafts.setDraftChannelChoice(channelHost, first, {
  channel: "wechat",
  account_id: "work",
});
channelDrafts.switchDraftChannelChoice(channelHost, first, second);
assert.equal(channelHost._pendingChannelChoice, null);
channelDrafts.setDraftChannelChoice(channelHost, second, {
  channel: "slack",
  account_id: "team",
});
channelDrafts.switchDraftChannelChoice(channelHost, second, first);
assert.deepEqual(channelHost._pendingChannelChoice, {
  channel: "wechat",
  account_id: "work",
});
globalThis.window.__pendingChannelChoices = channelHost.__pendingChannelChoices;
globalThis.window._pendingChannelChoice = channelHost._pendingChannelChoice;
assert.equal(sendModule.sendChatMessage({
  text: "channel draft",
  sessionId: first,
  thinking: "medium",
  toolsEnabled: true,
  webSearchEnabled: false,
}), true);
assert.equal(sent.at(-1).channel, "wechat");
assert.equal(sent.at(-1).account_id, "work");
channelDrafts.dropDraftChannelChoice(channelHost, first);
assert.equal(channelDrafts.draftChannelChoiceFor(channelHost, first), null);

const chatHandlers = readFileSync(
  new URL("../lib/runtime-bridge/chat-handlers.ts", import.meta.url),
  "utf8",
);
assert.match(chatHandlers, /const sid = responseSessionId\(data, W\.currentSessionId\);/);
assert.match(chatHandlers, /if \(responseTargetsActiveChat\(data, W\.currentSessionId\)\) \{\s*setRunActive\(false\);/);

const attachmentHook = readFileSync(
  new URL("../components/chat/composer/attach/use-composer-attachments.ts", import.meta.url),
  "utf8",
);
assert.ok(
  attachmentHook.indexOf("loadedRef.current = sid") <
    attachmentHook.indexOf("void loadAttachments(sid)"),
  "a chat switch must suppress saving the outgoing attachments under the incoming key",
);

console.log("multi-draft checks passed");
