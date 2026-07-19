import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { registerHooks } from "node:module";

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith("@/")) {
      return {
        url: new URL(`../${specifier.slice(2)}.ts`, import.meta.url).href,
        shortCircuit: true,
      };
    }
    return nextResolve(specifier, context);
  },
});

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

const { useCenterTabs } = await import("../lib/state/center-tabs-store.ts");
const {
  isDesktopSplitLayoutAvailable,
  isWebTabReady,
  setDesktopSplitLayoutAvailable,
  setWebTabReady,
  waitForWebTabReady,
} = await import("../lib/desktop-bridge.ts");

setWebTabReady("already-ready", true);
assert.equal(isWebTabReady("already-ready"), true);
assert.equal(await waitForWebTabReady("already-ready", 10), true);

const waiting = waitForWebTabReady("becomes-ready", 10);
setWebTabReady("becomes-ready", true);
assert.equal(await waiting, true);

assert.equal(await waitForWebTabReady("never-ready", 1), false);

setWebTabReady("clear-ready", true);
setWebTabReady("clear-ready", false);
assert.equal(isWebTabReady("clear-ready"), false);

setDesktopSplitLayoutAvailable(true);
assert.equal(isDesktopSplitLayoutAvailable(), true);
setDesktopSplitLayoutAvailable(false);
assert.equal(isDesktopSplitLayoutAvailable(), false);

const webTabPaneSource = await readFile(
  new URL("../components/center-tabs/web-tab-pane.tsx", import.meta.url),
  "utf8",
);
assert.match(webTabPaneSource, /setWebTabReady/);
assert.doesNotMatch(
  webTabPaneSource,
  /bridge\.webTab\.navigate\(tabId, viewUrlRef\.current\);/,
);
assert.match(webTabPaneSource, /const occluded = !!document\.querySelector/);
assert.match(
  webTabPaneSource,
  /occluded \|\| r\.width <= 0 \|\| r\.height <= 0/,
);
assert.ok(
  (webTabPaneSource.match(/setWebTabReady\(tabId, false\);/g) ?? []).length >= 2,
  "native view readiness must clear for hidden bounds and cleanup",
);
assert.match(webTabPaneSource, /new MutationObserver\(report\)/);

const fileTilesSource = await readFile(
  new URL("../components/chat/composer/attach/file-tiles.tsx", import.meta.url),
  "utf8",
);
assert.match(fileTilesSource, /data-native-view-occluder="true"/);

useCenterTabs.setState({
  tabs: [{ id: "s:chat", kind: "session", title: "Chat", sessionId: "chat" }],
  activeId: "s:chat",
  splitWebTabId: null,
  splitRatio: 0.44,
});
const id = useCenterTabs.getState().openWebTabInSplit("https://example.com/");
assert.equal(id, "w:https://example.com/");
assert.equal(useCenterTabs.getState().activeId, "s:chat");
assert.equal(useCenterTabs.getState().splitWebTabId, id);
assert.deepEqual(JSON.parse(values.get("openprogram.webSplit")), {
  tabId: id,
  ratio: 0.44,
});
useCenterTabs.setState({
  tabs: [
    { id: "s:chat", kind: "session", title: "Chat", sessionId: "chat" },
    { id, kind: "web", title: "other.example", url: "https://other.example/" },
  ],
  activeId: "s:chat",
  splitWebTabId: id,
  splitRatio: 0.44,
});
useCenterTabs.getState().openWebTabInSplit("https://example.com/");
assert.equal(useCenterTabs.getState().activeId, "s:chat");
assert.deepEqual(useCenterTabs.getState().tabs.find((tab) => tab.id === id), {
  id,
  kind: "web",
  title: "example.com",
  url: "https://example.com/",
});
useCenterTabs.getState().setSplitRatio(5);
assert.equal(useCenterTabs.getState().splitRatio, 0.70);
assert.deepEqual(JSON.parse(values.get("openprogram.webSplit")), {
  tabId: id,
  ratio: 0.70,
});
useCenterTabs.getState().setSplitWebTab(null);
assert.equal(useCenterTabs.getState().splitWebTabId, null);
useCenterTabs.getState().setSplitWebTab(id);
assert.equal(useCenterTabs.getState().splitWebTabId, id);
useCenterTabs.getState().closeTab(id);
assert.equal(useCenterTabs.getState().splitWebTabId, null);
assert.deepEqual(JSON.parse(values.get("openprogram.webSplit")), {
  tabId: null,
  ratio: 0.70,
});

values.set("centerTabs", JSON.stringify({
  tabs: [
    { id: "s:chat", kind: "session", title: "Chat", sessionId: "chat" },
    { id, kind: "web", title: "example.com", url: "https://example.com/" },
  ],
  activeId: "s:chat",
}));
values.set("openprogram.webSplit", JSON.stringify({ tabId: id, ratio: 5 }));
const { useCenterTabs: restoredSplit } = await import(
  "../lib/state/center-tabs-store.ts?restore-valid-split",
);
assert.equal(restoredSplit.getState().splitWebTabId, id);
assert.equal(restoredSplit.getState().splitRatio, 0.70);

values.set("openprogram.webSplit", JSON.stringify({ tabId: "s:chat", ratio: 0 }));
const { useCenterTabs: restoredInvalidSplit } = await import(
  "../lib/state/center-tabs-store.ts?restore-invalid-split",
);
assert.equal(restoredInvalidSplit.getState().splitWebTabId, null);
assert.equal(restoredInvalidSplit.getState().splitRatio, 0.30);

console.log("web-split checks passed");
