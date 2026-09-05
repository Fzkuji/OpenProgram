import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { createRequire, registerHooks } from "node:module";
import test from "node:test";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import { parseHTML } from "linkedom";

// Real React hooks, stores, WS dispatch and Desktop recovery; the router,
// network, CDN and Electron process transport are fixture boundaries only.
const webRoot = new URL("../", import.meta.url);
registerHooks({
  resolve(specifier, context, nextResolve) {
    const stubs = {
      "next/navigation": "export const usePathname = () => globalThis.reopenRouteHook(); export const useRouter = () => globalThis.reopenRouter;",
      "@/lib/external-libs": "export const externalLibsReady = async () => {};",
    };
    if (specifier in stubs) return { url: `data:text/javascript,${encodeURIComponent(stubs[specifier])}`, shortCircuit: true };
    if (specifier.endsWith(".module.css")) return { url: "data:text/javascript,export default {}", shortCircuit: true };
    const base = specifier.startsWith("@/") ? new URL(specifier.slice(2), webRoot).href
      : specifier.startsWith(".") && !/\.[a-z]+$/i.test(specifier) ? new URL(specifier, context.parentURL).href : null;
    if (base) for (const suffix of [".ts", ".tsx", "/index.ts", "/index.tsx"]) {
      if (existsSync(fileURLToPath(base + suffix))) return { url: base + suffix, shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    if (url.endsWith(".tsx")) return { format: "module", shortCircuit: true,
      source: ts.transpileModule(readFileSync(fileURLToPath(url), "utf8"), {
        compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
      }).outputText };
    return nextLoad(url, context);
  },
});
const { window } = parseHTML("<!doctype html><html><body></body></html>");
Object.assign(globalThis, { window, document: window.document, Event: window.Event,
  CustomEvent: window.CustomEvent, IS_REACT_ACT_ENVIRONMENT: true });
function storage() {
  const values = new Map();
  return { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key), clear: () => values.clear() };
}
globalThis.localStorage = storage();
globalThis.sessionStorage = storage();
window.location = { pathname: "/chat", hash: "", search: "", protocol: "http:", host: "127.0.0.1:18100" };
globalThis.location = window.location;
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
globalThis.requestAnimationFrame = (fn) => setTimeout(fn, 0);
globalThis.cancelAnimationFrame = clearTimeout;
globalThis.fetch = window.fetch = async () => Response.json({});
const routeListeners = new Set();
let navigations = [], recovery, ctx, ackRequests, ackTasks;
function navigate(path) {
  navigations.push(path);
  window.location.pathname = path;
  recovery?.observeNavigation(ctx, `http://127.0.0.1:18100${path}`);
  for (const notify of routeListeners) notify();
}
window.history = { state: null, pushState: (_state, _title, path) => navigate(path),
  replaceState: (_state, _title, path) => { window.location.pathname = path; window.location.hash = ""; } };
globalThis.reopenRouter = { push: navigate };
const { act, createElement, useSyncExternalStore } = await import("react");
globalThis.reopenRouteHook = () => useSyncExternalStore(
  (notify) => { routeListeners.add(notify); return () => routeListeners.delete(notify); },
  () => window.location.pathname,
);
const { createRoot } = await import("react-dom/client");
const { useTabLifecycle } = await import("../components/center-tabs/use-tab-lifecycle.ts");
const { useCenterTabs } = await import("../lib/state/center-tabs-store.ts");
const { readCenterTabsPayload, persistedState } = await import("../lib/state/center-tabs-persistence.ts");
const { useSessionStore } = await import("../lib/session-store/index.ts");
const { runtimeState } = await import("../lib/runtime-bridge/state.ts");
const { useWS } = await import("../lib/net/use-ws.ts");
const require = createRequire(import.meta.url);
const { createSelfUpdateReopen } = require("../../desktop/self-update-reopen.js");
const origin = "http://127.0.0.1:18100";
let sockets = [];
class FixtureSocket {
  static OPEN = 1;
  readyState = 1;
  sent = [];
  constructor(url) { assert.equal(url, `${origin.replace("http", "ws")}/ws`); sockets.push(this); }
  send(raw) {
    if (raw === "ping") return;
    const value = JSON.parse(raw);
    this.sent.push(value);
    if (value.action === "list_branches") queueMicrotask(() => this.message("branches_list", {
      session_id: value.session_id, branches: [],
    }));
  }
  message(type, data) { this.onmessage?.({ data: JSON.stringify({ type, data }) }); }
  close() { this.readyState = 3; }
}
globalThis.WebSocket = FixtureSocket;
const noop = () => {};
let lifecycle;
function Receiver() {
  const activeId = useCenterTabs((s) => s.activeId);
  lifecycle = useTabLifecycle({ activeId, cancelDrag: noop, setFocusedTabId: noop,
    freezeWidthsForMouseClose: noop, releaseFrozenWidths: noop });
  useWS();
  return createElement("div", {}, ...lifecycle.tabs.map((tab) => createElement("button", {
    key: tab.id, "data-tab": tab.id, onClick: () => lifecycle.onTabClick(tab),
  }, tab.title || tab.id)));
}

async function setup(tabs, activeId, windowId = "main") {
  localStorage.clear(); sessionStorage.clear(); sockets = []; navigations = []; ackRequests = []; ackTasks = [];
  window.location.pathname = "/chat";
  const intent = { schema: 1, update_id: "su_test", session_id: "origin", attempt: 1,
    reopen_id: "a".repeat(64), launch_kind: "activation", status: "pending", expires_at: Date.now() / 1000 + 60 };
  ctx = { id: windowId, win: { isDestroyed: () => false, webContents: {
    getURL: () => origin + window.location.pathname, send: noop,
  } } };
  recovery = createSelfUpdateReopen({ argv: ["--openprogram-self-update=su_test"], origin,
    fetchImpl: async (_url, init) => {
      if (init.method === "POST") {
        ackRequests.push(JSON.parse(init.body));
        // Observe the actual reducer state at the outgoing ACK boundary.
        assert.equal(useSessionStore.getState().messagesById["restored-message"]?.content, "restored transcript");
        assert.deepEqual(useSessionStore.getState().messageOrder.origin, ["restored-message"]);
      }
      return Response.json({ ...intent, status: init.method === "POST" ? "acknowledged" : "pending" });
    } });
  const startUrl = await recovery.resolveStartUrl(ctx, `${origin}/chat#token=${"x".repeat(43)}`);
  window.location.pathname = new URL(startUrl).pathname;
  window.openprogramDesktop = { isDesktop: true, windowId, selfUpdateReopen: {
    sessionLoaded: (sid) => { const result = recovery.sessionLoaded(ctx, sid); ackTasks.push(result); return result; },
  } };
  localStorage.setItem(`centerTabs:${windowId}`, JSON.stringify({ version: 2, tabs, activeId, groups: [], splitWebTabId: null, splitRatio: .5 }));
  useCenterTabs.setState(persistedState(readCenterTabsPayload()));
  useSessionStore.setState({ currentSessionId: null, activeChatKey: null, conversations: {}, messagesById: {}, messageOrder: {} });
  runtimeState.conversations = {};
  runtimeState.currentSessionId = null;
}
function transcript(socket, id = "origin") {
  socket.message("session_loaded", { id, title: "Original conversation", messages: [
    { id: "restored-message", role: "user", content: "restored transcript", timestamp: 100 },
  ], graph: [], settings: {}, head_id: "restored-message" });
}
async function mounted(check) {
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(Receiver)));
    assert.equal(sockets.length, 1);
    await check(host, root, sockets[0]);
  } finally {
    await act(async () => root.unmount()); host.remove();
    await Promise.all(ackTasks);
  }
}
const other = { id: "s:other", kind: "session", sessionId: "other", title: "Other" };
const original = { id: "s:origin", kind: "session", sessionId: "origin", title: "Original" };
for (const [name, tabs, activeId] of [
  ["other session", [other], other.id],
  ["existing origin", [other, original], other.id],
  ["draft", [{ id: "s:local_draft", kind: "session", sessionId: "local_draft", draft: true, title: "Draft" }], "s:local_draft"],
]) test(`reopen restores origin once over persisted ${name} and ACKs only a loaded transcript`, async () => {
  await setup(tabs, activeId);
  await mounted(async (host, root, socket) => {
    assert.equal(window.location.pathname, "/s/origin");
    assert.deepEqual(ackRequests, []);
    // AppShell's route synchronization arrives after child effects on mount.
    await act(async () => useSessionStore.getState().setCurrentConv("origin"));
    await act(async () => socket.onopen());
    assert.ok(socket.sent.some((v) => v.action === "load_session" && v.session_id === "origin"));
    assert.equal(useCenterTabs.getState().activeId, "s:origin");
    assert.equal(host.querySelectorAll('[data-tab="s:origin"]').length, 1);
    assert.deepEqual(ackRequests, []);
    await act(async () => { transcript(socket); await Promise.all(ackTasks); });
    assert.equal(ackRequests.length, 1);
    assert.equal(recovery.state().status, "acknowledged");
    await act(async () => { transcript(socket); await Promise.all(ackTasks); });
    assert.equal(ackRequests.length, 1);
    assert.equal(host.querySelectorAll('[data-tab="s:origin"]').length, 1);
    assert.ok(!navigations.includes("/s/other") && !navigations.includes("/chat"));
  });
});

test("manual tab selection before loading cancels relocation and late ACK", async () => {
  await setup([other, original], other.id);
  await mounted(async (host, root, socket) => {
    await act(async () => useSessionStore.getState().setCurrentConv("origin"));
    await act(async () => host.querySelector('[data-tab="s:other"]').click());
    assert.equal(window.location.pathname, "/s/other");
    assert.equal(recovery.state().status, "manual_navigation");
    await act(async () => { transcript(socket); await Promise.all(ackTasks); });
    assert.deepEqual(ackRequests, []);
    const url = await recovery.resolveStartUrl(ctx, `${origin}/chat#token=${"x".repeat(43)}`);
    assert.equal(new URL(url).pathname, "/chat");
    assert.equal(window.location.pathname, "/s/other");
    assert.equal(host.querySelectorAll('[data-tab="s:origin"]').length, 1);
  });
});

test("remount before ACK reuses the persisted origin tab", async () => {
  await setup([other], other.id);
  await mounted(async () => {
    await act(async () => useSessionStore.getState().setCurrentConv("origin"));
    assert.equal(useCenterTabs.getState().activeId, "s:origin");
    assert.deepEqual(ackRequests, []);
  });
  assert.equal(sockets[0].readyState, 3);
  useCenterTabs.setState(persistedState(readCenterTabsPayload()));
  sockets = [];
  await mounted(async (host, root, socket) => {
    assert.equal(host.querySelectorAll('[data-tab="s:origin"]').length, 1);
    assert.equal(host.querySelectorAll('[data-tab="s:other"]').length, 1);
    await act(async () => { transcript(socket); await Promise.all(ackTasks); });
    assert.equal(ackRequests.length, 1);
  });
});

test("detached window keeps its own persisted tabs and never ACKs main recovery", async () => {
  await setup([other], other.id, "detached");
  const mainTabs = JSON.stringify({ version: 2, tabs: [original], activeId: original.id });
  localStorage.setItem("centerTabs:main", mainTabs);
  await mounted(async (host, root, socket) => {
    assert.equal(host.querySelectorAll('[data-tab="s:origin"]').length, 0);
    assert.equal(useCenterTabs.getState().activeId, other.id);
    await act(async () => { transcript(socket); await Promise.all(ackTasks); });
    assert.deepEqual(ackRequests, []);
    assert.equal(localStorage.getItem("centerTabs:main"), mainTabs);
    assert.notEqual(window.location.pathname, "/s/origin");
  });
});
