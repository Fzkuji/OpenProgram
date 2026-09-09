import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import test from "node:test";
import { fileURLToPath } from "node:url";

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "@/lib/session-store") {
      return {
        url: new URL("../lib/session-store/index.ts", import.meta.url).href,
        shortCircuit: true,
      };
    }
    if (specifier.startsWith("@/")) {
      return {
        url: new URL(`../${specifier.slice(2)}.ts`, import.meta.url).href,
        shortCircuit: true,
      };
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

const listeners = new Map();
const storage = new Map();
globalThis.window = {
  fetch: globalThis.fetch,
  addEventListener(type, handler) {
    listeners.set(type, handler);
  },
  dispatchEvent() {},
  location: { pathname: "/s/origin", hash: "" },
};
globalThis.localStorage = {
  getItem: (key) => storage.get(key) ?? null,
  setItem: (key, value) => storage.set(key, String(value)),
  removeItem: (key) => storage.delete(key),
};
globalThis.WebSocket = { OPEN: 1 };

const { useCenterTabs } = await import("../lib/state/center-tabs-store.ts");
const { setSocket } = await import("../lib/runtime-bridge/state.ts");
const {
  installDesktopMenuHandlers,
  subscribeBrowserHumanInput,
  ensureWebView,
  destroyStaleWebViews,
  surfaceRefForChat,
  finalizeWebTabPreview,
  registerVisibleWebTabBounds,
  removeVisibleWebTabBounds,
  setWebTabReady,
  setDesktopSplitLayoutAvailable,
} = await import("../lib/desktop-bridge.ts");

function transferStub() {
  const unsubscribe = () => {};
  return {
    onRemoveSource: () => unsubscribe,
    onUndoDestination: () => unsubscribe,
    onCommitted: () => unsubscribe,
    onRejected: () => unsubscribe,
    onRolledBack: () => unsubscribe,
    onFinalizeOrphaned: () => unsubscribe,
    onStageIncoming: () => unsubscribe,
    pendingTerminal: async () => [],
    claimPending: async () => null,
  };
}


test("agent-attributed opening and popups retain background Pages without stealing a branch", async () => {
  const sent = [], resolved = [], activated = [];
  window.openprogramDesktop = {
    isDesktop: true, windowId: "main", openExternal() {},
    webTab: {
      ensure() {}, navigate() {}, async resolve(id) { resolved.push(id); return `target:${id}`; },
      async activate(id) { activated.push(id); return `target:${id}`; },
      preview: async () => null, capture: async () => null,
      setBounds() {}, show() {}, hide() {}, syncVisible() {}, destroy() {},
      reload() {}, stop() {}, goBack() {}, goForward() {},
      onState: () => () => {}, onPopup: () => () => {},
    }, tabTransfer: transferStub(), updates: {},
  };
  setSocket({ readyState: WebSocket.OPEN, send: payload => sent.push(JSON.parse(payload)) });
  installDesktopMenuHandlers();
  const viewer = { id: "s:other", kind: "session", sessionId: "other", title: "Other branch" };
  const owner = { id: "s:origin", kind: "session", sessionId: "origin", title: "Origin" };
  const manual = { id: "w:user", kind: "web", url: "https://manual.test", title: "Manual page" };
  useCenterTabs.setState({ tabs: [viewer, owner, manual], activeId: manual.id, groups: [], splitWebTabId: null });
  window.location.pathname = "/settings";
  for (let index = 0; index < 5; index++) {
    listeners.get("op:ws-message")({ detail: { type: "webtab.command", data: {
      op: "open", url: `https://retained.test/${index}`, req_id: `open:${index}`,
      session_id: "origin", execution_id: "execution-a", branch_id: "branch-a", window_id: "main",
    } } });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(useCenterTabs.getState().activeId, manual.id, "an agent may not switch the user's tab or conversation");
    const openResult = sent.find((message) => message.action === "webtab_result" && message.req_id === `open:${index}`);
    assert.equal(openResult?.ok, true, "background opens must not depend on a visible chat route");
  }
  assert.equal(resolved.length, 5);
  assert.deepEqual(activated, []);
  const state = useCenterTabs.getState();
  const pages = state.tabs.filter(tab => tab.agentOpened);
  assert.equal(pages.length, 5);
  assert.ok(pages.every(tab => tab.agentSessionId === "origin"));
  assert.ok(pages.every(tab => tab.agentBranchId === "branch-a"));
  assert.ok(pages.every(tab => tab.agentExecutionId === "execution-a"));
  assert.ok(pages.every(tab => !tab.webPinned));
  const popupId = state.openPopupWebTab("https://retained.test/popup", pages[0].id);
  assert.equal(useCenterTabs.getState().activeId, manual.id, "an agent popup must stay in Resources");
  const popup = useCenterTabs.getState().tabs.find(tab => tab.id === popupId);
  assert.equal(popup.agentBranchId, "branch-a");
  assert.equal(popup.agentExecutionId, "execution-a");
  assert.equal(popup.openerTabId, pages[0].id);
  const humanPopupId = state.openPopupWebTab("https://manual.test/popup", manual.id);
  assert.equal(useCenterTabs.getState().activeId, humanPopupId, "manual popup behavior remains available");
});


test("a selected image mirror supplies exact Page context without native visibility or focus", async () => {
  const { useWebTabPip } = await import("../lib/state/web-tab-pip-store.ts");
  const page = { id: "w:mirror", kind: "web", title: "Mirror", url: "https://mirror.test/" };
  useCenterTabs.setState({ tabs: [
    { id: "s:origin", kind: "session", sessionId: "origin", title: "Origin" },
    { id: "s:other", kind: "session", sessionId: "other", title: "Other" }, page,
  ], activeId: "s:origin", groups: [], splitWebTabId: null });
  useWebTabPip.getState().show(page.id, "s:origin");
  assert.equal(surfaceRefForChat("origin", true)?.tab_id, page.id);
  assert.equal(surfaceRefForChat("origin", true)?.background, true);
  assert.equal(surfaceRefForChat("other", true), null);
  const sent = [], calls = [];
  setSocket({ readyState: WebSocket.OPEN, send: payload => sent.push(JSON.parse(payload)) });
  window.openprogramDesktop.webTab.preview = async (id, background) => {
    calls.push([id, background]); return { target_id: "mirror-target", tab_id: id, preview: {} };
  };
  listeners.get("op:ws-message")({ detail: { type: "webtab.command", data: {
    op: "preview", req_id: "mirror-context", window_id: "main", tab_id: page.id, background: true,
  } } });
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(calls, [[page.id, true]]);
  assert.equal(sent.find((message) => message.action === "webtab_result" && message.req_id === "mirror-context")?.ok, true);
  assert.equal(useCenterTabs.getState().activeId, "s:origin");
  useCenterTabs.getState().setActive("s:other");
  assert.equal(surfaceRefForChat("origin", true), null);
  assert.equal(finalizeWebTabPreview(page.id, 0, { target_id: "mirror-target" }, true).ok, false,
    "a late mirror observation must be rejected after its selected owner changes");
  useWebTabPip.getState().end();
});


test("native human input is forwarded by exact Page even without an open Resources panel", async () => {
  const sent = [], cleared = [], notices = [];
  let listener;
  const oldDispatch = window.dispatchEvent;
  window.dispatchEvent = event => { notices.push(event.detail); };
  setSocket({ readyState: WebSocket.OPEN, send: payload => sent.push(JSON.parse(payload)) });
  useCenterTabs.setState({ tabs: [{ id: "w:human", kind: "web", title: "Page", url: "https://human.test" }], activeId: "w:human", groups: [] });
  const dispose = subscribeBrowserHumanInput({ windowId: "main", webTab: {
    onHumanInput(callback) { listener = callback; return () => { listener = null; }; },
    async showAction(id, marker) { cleared.push([id, marker]); return true; },
  } });
  listener({ id: "w:human", windowId: "foreign", sequence: 1, kind: "pointer" });
  listener({ id: "w:closed", windowId: "main", sequence: 1, kind: "key" });
  assert.equal(sent.length, 0);
  listener({ id: "w:human", windowId: "main", sequence: 1, kind: "pointer" });
  listener({ id: "w:human", windowId: "main", sequence: 1, kind: "pointer" });
  assert.deepEqual(sent, [{ action: "webtab_human_input", tab_id: "w:human", window_id: "main", sequence: 1, kind: "pointer" }]);
  assert.deepEqual(cleared, [["w:human", null]]);
  assert.equal(notices[0].connected, true);
  setSocket(null);
  listener({ id: "w:human", windowId: "main", sequence: 2, kind: "scroll" });
  assert.equal(notices.at(-1).connected, false);
  assert.equal(sent.length, 1, "disconnected input is visible locally without claiming a successful pause");
  dispose();
  window.dispatchEvent = oldDispatch;
});


test("closing a retained native Page reports its exact lifecycle once", () => {
  const sent = [], destroyed = [];
  setSocket({ readyState: WebSocket.OPEN, send: payload => sent.push(JSON.parse(payload)) });
  const bridge = { windowId: "main", webTab: {
    ensure() {}, syncVisible() {}, destroy(id) { destroyed.push(id); },
  } };
  ensureWebView(bridge, "w:closing-retained", "https://close.test");
  destroyStaleWebViews(bridge, []);
  assert.ok(destroyed.includes("w:closing-retained"));
  assert.deepEqual(sent.filter(message => message.tab_id === "w:closing-retained"), [
    { action: "webtab_closed", tab_id: "w:closing-retained", window_id: "main" },
  ]);
  const count = sent.length;
  destroyStaleWebViews(bridge, []);
  assert.equal(sent.length, count);
});

test("dispatch and receipt share one native cue and stale geometry cannot paint", async () => {
  const { resetBrowserResources, setBrowserConnection } = await import("../lib/state/session-resources.ts");
  resetBrowserResources();
  setBrowserConnection(true);
  const calls = [];
  window.openprogramDesktop.webTab.showAction = async (id, marker) => { calls.push([id, marker]); return true; };
  useCenterTabs.setState({ tabs: [{ id: "w:cue", kind: "web", title: "Cue", url: "https://cue.test" }], activeId: "w:cue", groups: [] });
  const row = { source: "browser", id: "association-cue", resource_id: "page-cue", session_id: "origin", conversation_session_id: "origin",
    tab_id: "w:cue", window_id: "main", kind: "web", title: "Cue", target: "https://cue.test", status: "in_use", control_state: "active",
    generation: 1, sequence: 1, last_operation: { id: "click-1", action: "click", phase: "dispatched", frame_id: "frame-1", geometry_revision: 0,
      point: { x: 400, y: 300, width: 800, height: 600 } },
  };
  const receive = data => listeners.get("op:ws-message")({ detail: { type: "browser.resource", data } });
  receive(row);
  receive({ ...row, sequence: 2, last_operation: { ...row.last_operation, phase: "acknowledged" } });
  assert.equal(calls.length, 1, "acknowledgement must neither erase a current cue nor restart its TTL");
  assert.equal(calls[0][1].generation, 1);
  assert.equal(calls[0][1].resourceId, "page-cue", "native cue freshness includes the backend Page incarnation");
  receive({ ...row, sequence: 3, control_state: "paused" });
  assert.equal(calls.at(-1)[1], null);
  receive({ ...row, sequence: 4, last_operation: { ...row.last_operation, phase: "acknowledged" } });
  assert.equal(calls.length, 2, "an old operation must not recreate a cue after pausing");
  registerVisibleWebTabBounds(window.openprogramDesktop, "w:cue", { x: 0, y: 0, width: 900, height: 600 });
  receive({ ...row, sequence: 5, last_operation: { ...row.last_operation, id: "stale-click" } });
  assert.equal(calls.at(-1)[1], null, "the old geometry cannot be painted into a resized Page");
  removeVisibleWebTabBounds(window.openprogramDesktop, "w:cue");
});
