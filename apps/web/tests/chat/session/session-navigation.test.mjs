import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { createRequire, registerHooks } from "node:module";
import test, { after } from "node:test";
import { fileURLToPath } from "node:url";

import { parseHTML } from "linkedom";

const webRoot = new URL("../../../", import.meta.url);
const ts = createRequire(new URL("tests/quality-probe.mjs", webRoot))("typescript");
registerHooks({
  load(url, context, nextLoad) {
    if (!url.endsWith(".tsx")) return nextLoad(url, context);
    const source = ts.transpileModule(readFileSync(fileURLToPath(url), "utf8"), {
      compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
    }).outputText;
    return { format: "module", shortCircuit: true, source };
  },
  resolve(specifier, context, nextResolve) {
    if (specifier.endsWith(".module.css")) {
      return {
        url: "data:text/javascript,export default {}",
        shortCircuit: true,
      };
    }
    const resolveBase = specifier.startsWith("@/")
      ? new URL(specifier.slice(2), webRoot).href
      : specifier.startsWith(".") && !/\.[a-z]+$/i.test(specifier)
        ? new URL(specifier, context.parentURL).href
        : null;
    if (resolveBase) {
      for (const suffix of [".ts", ".tsx", "/index.ts", "/index.tsx"]) {
        const url = `${resolveBase}${suffix}`;
        if (existsSync(fileURLToPath(url))) {
          return { url, shortCircuit: true };
        }
      }
    }
    return nextResolve(specifier, context);
  },
});

const parsed = parseHTML(
  '<!doctype html><html><body><div id="root"></div></body></html>',
);
globalThis.window = parsed.window;
globalThis.document = parsed.document;
globalThis.Event = parsed.window.Event;
globalThis.CustomEvent = parsed.window.CustomEvent;
globalThis.localStorage = {
  getItem() { return null; },
  setItem() {},
  removeItem() {},
};
globalThis.WebSocket = { OPEN: 1 };
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
Object.defineProperty(window, "location", {
  value: { pathname: "/chat" },
  configurable: true,
});
window.matchMedia = () => ({
  matches: false,
  addEventListener() {},
  removeEventListener() {},
});
const historyCalls = [];
globalThis.history = {
  pushState(_s, _t, path) { historyCalls.push(["push", path]); window.location.pathname = path; },
  replaceState(_s, _t, path) { historyCalls.push(["replace", path]); window.location.pathname = path; },
};
window.history = globalThis.history;
globalThis.navigator ??= { platform: "MacIntel" };
globalThis.fetch = async () => ({ ok: true, status: 200, async json() { return {}; } });

const sent = [];
const { runtimeState, setSocket } = await import("../../../lib/runtime-bridge/state.ts");
setSocket({ readyState: WebSocket.OPEN, send(payload) { sent.push(JSON.parse(payload)); } });
const { disposeSessionLoads } = await import("../../../lib/runtime-bridge/session/session-load.ts");
after(() => disposeSessionLoads(runtimeState.ws));

const { selectRow, useSessionStore } = await import("../../../lib/session-store/index.ts");
const { useCenterTabs, chatAckIsActive, sessionWasSent } = await import("../../../lib/tabs/center-tabs-store.ts");
const handlers = await import("../../../lib/runtime-bridge/session/chat-handlers.ts");
const { applyChatWsMessage, appendLocalUserTurn } = await import("../../../lib/net/ws/chat-stream.ts");
const pending = await import("../../../lib/chat/send/pending-user-text.ts");
const { openNewChat, deleteSessionAndRefocus } = await import("../../../lib/tabs/navigation/open-session.ts");
const { tabForShortcut, shortcutForKey } = await import("../../../lib/tabs/navigation/tab-shortcuts.ts");

const tabs = () => useCenterTabs.getState();
const activeTab = () => tabs().tabs.find((tab) => tab.id === tabs().activeId);
function resetTabs() {
  useCenterTabs.setState({ tabs: [], activeId: null, groups: [], splitWebTabId: null, navigationRoute: undefined });
  window.location.pathname = "/chat";
  historyCalls.length = 0;
  sent.length = 0;
}
function ack(data) {
  applyChatWsMessage({ type: "chat_ack", data });
  handlers.wsHandleChatAck(data);
}
function respond(data) {
  applyChatWsMessage({ type: "chat_response", data });
  handlers.wsHandleChatResponse(data);
}
/** The composer's send, as far as the client state goes. */
function send(sid, text) {
  pending.setPendingUserText(sid, text, Date.now(), { messageId: `pending_${sid}` });
  if (sid.startsWith("local_")) pending.setPendingFirstAck(sid);
  appendLocalUserTurn(sid, `pending_${sid}`, text, undefined, Date.now(), "pending");
  useSessionStore.getState().setRunningTaskFor(sid, { session_id: sid, msg_id: "", started_at: Date.now() / 1000 });
}

test("an ack from a channel conversation never takes over the open tab", () => {
  resetTabs();
  tabs().openSessionTab("open-chat", "Open");
  runtimeState.currentSessionId = "open-chat";
  window.location.pathname = "/s/open-chat";
  assert.equal(chatAckIsActive("wechat-chat"), false);
  ack({ session_id: "wechat-chat", msg_id: "w1" });
  assert.equal(runtimeState.currentSessionId, "open-chat");
  assert.equal(window.location.pathname, "/s/open-chat");
  assert.equal(activeTab().sessionId, "open-chat");
});

test("the first ack replaces the draft's /chat entry instead of adding one", () => {
  resetTabs();
  const draft = tabs().openDraftSessionTab();
  useSessionStore.getState().setCurrentDraft(draft);
  send(draft, "hello");
  ack({ session_id: draft, msg_id: "d1" });
  assert.deepEqual(historyCalls, [["replace", "/s/" + draft]]);
  assert.equal(activeTab().draft, false);
});

test("New chat does not reuse a draft whose first message is on its way", () => {
  resetTabs();
  const first = openNewChat();
  assert.equal(openNewChat(), first, "an empty draft is reused");
  send(first, "first message");
  const second = openNewChat();
  assert.notEqual(second, first);
  assert.equal(activeTab().sessionId, second);
  // The late ack for the first chat stays in its own background tab.
  ack({ session_id: first, msg_id: "f1" });
  assert.equal(activeTab().sessionId, second);
  assert.equal(useSessionStore.getState().activeChatKey, second);
  // A project group's + always gets a distinct draft.
  assert.notEqual(openNewChat({ fresh: true }), second);
});

test("/goal status during a run leaves the run going", () => {
  const sid = "goal-session";
  useSessionStore.getState().setRunningTaskFor(sid, { session_id: sid, msg_id: "turn1", execution_id: "exec1", started_at: 1 });
  pending.setPendingUserText(sid, "/goal", Date.now(), { messageId: "pending_goal" });
  respond({ type: "local_command", session_id: sid, command: "goal", content: "Goal: running" });
  assert.equal(useSessionStore.getState().runningTasks[sid]?.execution_id, "exec1");
  assert.equal(pending.hasPendingUserText(sid), false);

  // On an idle chat the command's own send reservation is released, and a
  // bubble drawn for it goes: it was no message, only its reply line stays.
  const idle = "goal-idle";
  send(idle, "/goal");
  respond({ type: "local_command", session_id: idle, command: "goal", content: "No goal" });
  assert.equal(useSessionStore.getState().runningTasks[idle], undefined);
  assert.equal(selectRow(idle, `pending_${idle}`)(useSessionStore.getState()), undefined);
});

test("an error without msg_id shows a toast and settles the rejected first send", () => {
  resetTabs();
  const toasts = [];
  const onToast = (event) => toasts.push(event.detail.message);
  window.addEventListener("op:toast", onToast);
  const draft = tabs().openDraftSessionTab();
  send(draft, "/goal x");
  respond({ type: "error", session_id: draft, code: "page_context_stale", content: "The submitted Page context is stale." });
  window.removeEventListener("op:toast", onToast);
  assert.deepEqual(toasts, ["The submitted Page context is stale."]);
  assert.equal(pending.hasPendingFirstAck(draft), false);
  assert.equal(useSessionStore.getState().runningTasks[draft], undefined);
  assert.equal(selectRow(draft, `pending_${draft}`)(useSessionStore.getState()).status, "error");
  assert.equal(sessionWasSent(draft), false);
});

test("a reply not delivered to the bound channel shows a toast and leaves the run alone", () => {
  resetTabs();
  const toasts = [];
  const onToast = (event) => toasts.push(event.detail);
  window.addEventListener("op:toast", onToast);
  useSessionStore.getState().setRunningTaskFor("bound", { session_id: "bound", msg_id: "t2", execution_id: "e2", started_at: 1 });
  respond({
    type: "channel_delivery_failed", session_id: "bound", assistant_msg_id: "t1_reply",
    channel: "wechat", channel_label: "WeChat", error_kind: "auth", content: "x",
  });
  window.removeEventListener("op:toast", onToast);
  assert.equal(toasts.length, 1);
  assert.match(toasts[0].message, /WeChat/);
  assert.match(toasts[0].message, /auth/);
  assert.equal(toasts[0].tone, "error");
  assert.equal(useSessionStore.getState().runningTasks.bound?.execution_id, "e2");
});

test("an early turn error with a fresh msg_id releases the send's running slot", () => {
  resetTabs();
  const draft = tabs().openDraftSessionTab();
  send(draft, "hi");
  respond({ type: "error", session_id: draft, msg_id: "fresh01", code: "unknown_environment", content: "Environment gone" });
  assert.equal(useSessionStore.getState().runningTasks[draft], undefined);
  assert.equal(pending.hasPendingFirstAck(draft), false);
  // A running turn's slot is untouched by an unrelated error.
  useSessionStore.getState().setRunningTaskFor("busy", { session_id: "busy", msg_id: "t1", execution_id: "e1", started_at: 1 });
  respond({ type: "error", session_id: "busy", content: "compact failed" });
  assert.equal(useSessionStore.getState().runningTasks.busy?.execution_id, "e1");
});

test("deleting the open chat focuses its neighbour; the last one leaves a New chat", () => {
  resetTabs();
  for (const id of ["A", "B", "C"]) {
    useSessionStore.getState().upsertConversation({ id, title: id });
    tabs().openSessionTab(id, id, { newTab: true });
  }
  tabs().setActive(tabs().tabs.find((tab) => tab.sessionId === "B").id);
  // A tab that showed something before the deleted chat goes back to it.
  useSessionStore.getState().upsertConversation({ id: "D", title: "D" });
  tabs().openSessionTab("D", "D", { newTab: true });
  tabs().openSessionTab("E", "E");
  useSessionStore.getState().upsertConversation({ id: "E", title: "E" });
  deleteSessionAndRefocus("E");
  assert.equal(activeTab().sessionId, "D");
  tabs().closeTab(activeTab().id);
  tabs().setActive(tabs().tabs.find((tab) => tab.sessionId === "B").id);
  deleteSessionAndRefocus("B");
  assert.ok(sent.some((m) => m.action === "delete_session" && m.session_id === "B"));
  assert.equal(activeTab().sessionId, "C");
  assert.equal(useSessionStore.getState().conversations.B, undefined);
  deleteSessionAndRefocus("C");
  assert.equal(activeTab().sessionId, "A");
  deleteSessionAndRefocus("A");
  assert.equal(tabs().tabs.length, 1);
  assert.equal(activeTab().draft, true);
});

test("a finished turn marks a background chat unread, never the one on screen", () => {
  resetTabs();
  for (const id of ["shown", "background"]) {
    runtimeState.conversations[id] = { id, title: id, messages: [] };
    useSessionStore.getState().upsertConversation({ id, title: id });
  }
  tabs().openSessionTab("shown", "shown");
  runtimeState.currentSessionId = "shown";
  window.location.pathname = "/s/shown";
  handlers.handleSessionUpdated({ id: "background", status: "done", unread: true });
  handlers.handleSessionUpdated({ id: "shown", status: "failed", unread: true });
  const convs = useSessionStore.getState().conversations;
  assert.equal(convs.background.unread, true);
  assert.equal(convs.background.status, "done");
  assert.equal(convs.shown.unread, false);
  assert.ok(sent.some((m) => m.action === "mark_session_read" && m.session_id === "shown"));
  assert.ok(!sent.some((m) => m.action === "mark_session_read" && m.session_id === "background"));
  // Opening it clears the dot at once.
  handlers.markSessionReadLocally("background");
  assert.equal(useSessionStore.getState().conversations.background.unread, false);
});

test("a chat in a hidden window keeps its dot until the window comes back", (t) => {
  let visibility = "hidden";
  Object.defineProperty(document, "visibilityState", { get: () => visibility, configurable: true });
  t.after(() => { delete document.visibilityState; });
  resetTabs();
  runtimeState.conversations.open = { id: "open", title: "open", messages: [] };
  useSessionStore.getState().upsertConversation({ id: "open", title: "open" });
  tabs().openSessionTab("open", "open");
  runtimeState.currentSessionId = "open";
  window.location.pathname = "/s/open";

  handlers.windowVisibilityChanged();
  handlers.handleSessionUpdated({ id: "open", status: "done", unread: true });

  assert.deepEqual(sent, [{ action: "set_window_hidden", hidden: true }]);
  assert.equal(useSessionStore.getState().conversations.open.unread, true);

  sent.length = 0;
  visibility = "visible";
  handlers.windowVisibilityChanged();

  assert.deepEqual(sent, [
    { action: "set_window_hidden", hidden: false },
    { action: "mark_session_read", session_id: "open" },
  ]);
  assert.equal(useSessionStore.getState().conversations.open.unread, false);

  // Nothing new while it was away: only the flag goes out.
  sent.length = 0;
  handlers.windowVisibilityChanged();
  assert.deepEqual(sent, [{ action: "set_window_hidden", hidden: false }]);
});

test("a reconnect tells the server the window is hidden before it restores focus", () => {
  const source = readFileSync(new URL("lib/net/ws/use-ws.ts", webRoot), "utf8");
  const hidden = source.indexOf('action: "set_window_hidden"');
  const focus = source.indexOf('action: "mark_session_read"');
  assert.ok(hidden !== -1 && hidden < focus);
});

test("Cmd+1…9 and Cmd+Shift+[ ] pick strip entries", () => {
  resetTabs();
  for (const id of ["T1", "T2", "T3"]) tabs().openSessionTab(id, id, { newTab: true });
  const ids = tabs().tabs.map((tab) => tab.id);
  tabs().setActive(ids[0]);
  assert.equal(tabForShortcut(tabs(), { action: "select-tab", index: 2 }), ids[1]);
  assert.equal(tabForShortcut(tabs(), { action: "select-tab", index: 9 }), ids[2]);
  assert.equal(tabForShortcut(tabs(), { action: "select-tab", index: 5 }), null);
  assert.equal(tabForShortcut(tabs(), { action: "previous-tab" }), ids[2]);
  assert.equal(tabForShortcut(tabs(), { action: "next-tab" }), ids[1]);
  const key = (k, extra = {}) => shortcutForKey({ key: k, code: "", metaKey: true, ctrlKey: false, altKey: false, shiftKey: false, ...extra }, true);
  assert.deepEqual(key("n"), { action: "new-chat" });
  assert.deepEqual(key("3"), { action: "select-tab", index: 3 });
  assert.deepEqual(key("}", { shiftKey: true, code: "BracketRight" }), { action: "next-tab" });
  assert.deepEqual(key("T", { shiftKey: true, code: "KeyT" }), { action: "reopen-tab" });
  assert.deepEqual(key(",", { code: "Comma" }), { action: "open-settings" }, "Cmd+, opens Settings, as the main menu shows");
  assert.equal(key("t"), null, "Cmd+T stays the new-tab menu item");
  assert.equal(key("n", { metaKey: false, ctrlKey: true }), null);
});

test("New chat reusing a draft leaves the old session at once; Stop then cancels the new execution", async () => {
  const { setNavigate } = await import("../../../lib/nav/navigate.ts");
  const { stopSession } = await import("../../../components/chat/composer/submit/use-chat-submit.ts");
  const { resolveFnFormSessionId } = await import("../../../components/chat/composer/modes/fn-form/session-target.ts");
  const routed = [];
  setNavigate((path) => routed.push(path));
  try {
    resetTabs();
    const draft = tabs().openDraftSessionTab();
    tabs().openNewTabPage();
    tabs().openSessionTab("old-chat", "Old");
    runtimeState.currentSessionId = "old-chat";
    useSessionStore.getState().setCurrentConv("old-chat");
    window.location.pathname = "/s/old-chat";

    assert.equal(openNewChat(), draft, "the empty draft is reused");
    // The same state a fresh draft gets: no old session, /chat right now. A
    // router push here committed later and reset the chat after the ACK.
    assert.equal(window.location.pathname, "/chat");
    assert.deepEqual(routed, []);
    assert.equal(runtimeState.currentSessionId, null);
    assert.equal(useSessionStore.getState().activeChatKey, draft);

    send(draft, "a long turn");
    ack({ session_id: draft, msg_id: "r1", execution_id: "exec-new", status_version: 3 });
    assert.equal(window.location.pathname, `/s/${draft}`);
    assert.equal(runtimeState.currentSessionId, draft);

    sent.length = 0;
    const state = useSessionStore.getState();
    const target = resolveFnFormSessionId(state.currentSessionId, state.activeChatKey);
    assert.equal(target, draft);
    stopSession(target, (payload) => { sent.push(payload); return true; });
    const cancel = sent.find((frame) => frame.action === "execution.cancel");
    assert.ok(cancel, "Stop sends execution.cancel");
    assert.equal(cancel.execution_id, "exec-new");
  } finally {
    setNavigate(null);
  }
});

test("a session opened with newTab keeps the active tab", () => {
  resetTabs();
  tabs().openSessionTab("running", "Running");
  tabs().openSessionTab("other", "Other", { newTab: true });
  assert.equal(tabs().tabs.length, 2);
  assert.equal(activeTab().sessionId, "other");
  assert.deepEqual(tabs().tabs.map((tab) => tab.sessionId), ["running", "other"]);
});
