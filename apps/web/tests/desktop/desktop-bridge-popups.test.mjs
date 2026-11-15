import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import test from "node:test";
import { fileURLToPath } from "node:url";

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "@/lib/session-store") {
      return {
        url: new URL("../../lib/session-store/index.ts", import.meta.url).href,
        shortCircuit: true,
      };
    }
    if (specifier.startsWith("@/")) {
      return {
        url: new URL(`../../${specifier.slice(2)}.ts`, import.meta.url).href,
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
  addEventListener(type, handler) {
    listeners.set(type, handler);
  },
  dispatchEvent(event) {
    listeners.get(event.type)?.(event);
  },
  location: { pathname: "/settings", hash: "" },
};
globalThis.localStorage = {
  getItem: (key) => storage.get(key) ?? null,
  setItem: (key, value) => storage.set(key, String(value)),
  removeItem: (key) => storage.delete(key),
};
globalThis.WebSocket = { OPEN: 1 };

const { setSocket } = await import("../../lib/runtime-bridge/state.ts");
const { useCenterTabs } = await import("../../lib/tabs/center-tabs-store.ts");
const { subscribeWebTabPopups } = await import("../../lib/desktop/bridge/bridge-events.ts");

function popupBridge() {
  let emit = () => {};
  const ensured = [], destroyed = [];
  const bridge = {
    windowId: "main",
    webTab: {
      onPopup: (handler) => { emit = handler; return () => {}; },
      ensure: (id, url) => ensured.push([id, url]),
      resolve: async (id) => `target:${id}`,
      destroy: (id) => destroyed.push(id),
    },
  };
  subscribeWebTabPopups(bridge);
  return { ensured, destroyed, popup: (detail) => emit(detail) };
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

test("an Agent's popup loads in the background and is handed to the worker", async () => {
  const sent = [];
  setSocket({ readyState: WebSocket.OPEN, send: (payload) => sent.push(JSON.parse(payload)) });
  const opener = {
    id: "w:agent", kind: "web", url: "https://visa.test/start", title: "Start",
    agentOpened: true, agentSessionId: "origin",
  };
  useCenterTabs.setState({ tabs: [opener], activeId: opener.id, groups: [], splitWebTabId: null });
  const { ensured, popup } = popupBridge();

  popup({ openerId: opener.id, url: "https://visa.test/apply" });
  await settle();

  const tab = useCenterTabs.getState().tabs.find((item) => item.openerTabId === opener.id);
  assert.ok(tab, "the popup is a tab of its own");
  assert.equal(useCenterTabs.getState().activeId, opener.id, "the user's view stays put");
  assert.deepEqual(ensured, [[tab.id, "https://visa.test/apply"]]);
  assert.deepEqual(sent, [{
    action: "webtab_popup", window_id: "main", tab_id: tab.id,
    opener_tab_id: opener.id, target_id: `target:${tab.id}`,
    url: "https://visa.test/apply", title: tab.title,
  }]);
});

test("a person's popup opens in front and is not reported as Agent work", async () => {
  const sent = [];
  setSocket({ readyState: WebSocket.OPEN, send: (payload) => sent.push(JSON.parse(payload)) });
  const opener = { id: "w:user", kind: "web", url: "https://manual.test/", title: "Manual" };
  useCenterTabs.setState({ tabs: [opener], activeId: opener.id, groups: [], splitWebTabId: null });
  const { ensured, popup } = popupBridge();

  popup({ openerId: opener.id, url: "https://manual.test/popup" });
  await settle();

  const tab = useCenterTabs.getState().tabs.find((item) => item.openerTabId === opener.id);
  assert.equal(useCenterTabs.getState().activeId, tab.id);
  assert.deepEqual(ensured, []);
  assert.deepEqual(sent, []);
});

test("a popup the desktop already made keeps its id, so its opener link survives", async () => {
  setSocket({ readyState: WebSocket.OPEN, send() {} });
  const opener = { id: "w:user", kind: "web", url: "https://visa.test/", title: "Visa" };
  useCenterTabs.setState({ tabs: [opener], activeId: opener.id, groups: [], splitWebTabId: null });
  const { popup } = popupBridge();

  popup({ openerId: opener.id, url: "https://visa.test/login", id: "w:https://visa.test/login:popup:x:n0" });

  assert.equal(useCenterTabs.getState().activeId, "w:https://visa.test/login:popup:x:n0");
});

test("a desktop-made popup with no opener tab is closed, not leaked", async () => {
  useCenterTabs.setState({ tabs: [], activeId: null, groups: [], splitWebTabId: null });
  const { destroyed, popup } = popupBridge();

  popup({ openerId: "w:gone", url: "https://visa.test/login", id: "native-popup" });

  assert.deepEqual(destroyed, ["native-popup"]);
  assert.deepEqual(useCenterTabs.getState().tabs, []);
});
