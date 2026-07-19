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
  SPLIT_CHAT_MIN_WIDTH,
  SPLIT_DIVIDER_WIDTH,
  SPLIT_WEB_MIN_WIDTH,
  clampSplitRatioForWidth,
  isSplitLayoutAvailable,
} = await import("../lib/split-layout.ts");
const {
  isDesktopSplitLayoutAvailable,
  isWebTabReady,
  restorePriorActiveTabAfterFailedWebOpen,
  setDesktopSplitLayoutAvailable,
  setWebTabReady,
  waitForWebTabReady,
} = await import("../lib/desktop-bridge.ts");

assert.equal(SPLIT_CHAT_MIN_WIDTH, 360);
assert.equal(SPLIT_WEB_MIN_WIDTH, 480);
assert.equal(SPLIT_DIVIDER_WIDTH, 6);
const thresholdWidth = 846;
const thresholdRatio = SPLIT_CHAT_MIN_WIDTH / thresholdWidth;
assert.equal(clampSplitRatioForWidth(0.44, thresholdWidth), thresholdRatio);
assert.equal(clampSplitRatioForWidth(0.70, thresholdWidth), thresholdRatio);
assert.equal(clampSplitRatioForWidth(0.44, 1200), 0.44);
assert.equal(
  clampSplitRatioForWidth(0.70, 1200),
  (1200 - SPLIT_WEB_MIN_WIDTH - SPLIT_DIVIDER_WIDTH) / 1200,
);
const preferredRatio = 0.527651858567543;
assert.equal(isSplitLayoutAvailable(864), true);
assert.equal(clampSplitRatioForWidth(preferredRatio, 864), 0.4375);
assert.equal(
  clampSplitRatioForWidth(preferredRatio, 1200),
  preferredRatio,
  "restoring space must recompute from the preferred ratio",
);
assert.equal(isSplitLayoutAvailable(463), false);

setWebTabReady("already-ready", true);
assert.equal(isWebTabReady("already-ready"), true);
assert.equal(await waitForWebTabReady("already-ready", 10), true);

const waiting = waitForWebTabReady("becomes-ready", 10);
setWebTabReady("becomes-ready", true);
assert.equal(await waiting, true);

assert.equal(await waitForWebTabReady("never-ready", 1), false);

const clearingWaiter = waitForWebTabReady("clear-while-waiting", 5);
setWebTabReady("clear-while-waiting", false);
assert.equal(await clearingWaiter, false);

setWebTabReady("clear-ready", true);
setWebTabReady("clear-ready", false);
assert.equal(isWebTabReady("clear-ready"), false);

setDesktopSplitLayoutAvailable(true);
assert.equal(isDesktopSplitLayoutAvailable(), true);
setDesktopSplitLayoutAvailable(false);
assert.equal(isDesktopSplitLayoutAvailable(), false);

const priorSessionId = "s:prior";
const fallbackWebId = "w:https://fallback.example/";
const userSelectedId = "s:user-selected";
const rollbackTabs = [
  { id: priorSessionId, kind: "session", title: "Prior", sessionId: "prior" },
  {
    id: fallbackWebId,
    kind: "web",
    title: "fallback.example",
    url: "https://fallback.example/",
  },
  {
    id: userSelectedId,
    kind: "session",
    title: "User selected",
    sessionId: "user-selected",
  },
];
useCenterTabs.setState({ tabs: rollbackTabs, activeId: fallbackWebId });
restorePriorActiveTabAfterFailedWebOpen(priorSessionId, fallbackWebId);
assert.equal(
  useCenterTabs.getState().activeId,
  priorSessionId,
  "failed fallback restores the prior tab while the opened web tab is active",
);
useCenterTabs.setState({ tabs: rollbackTabs, activeId: userSelectedId });
restorePriorActiveTabAfterFailedWebOpen(priorSessionId, fallbackWebId);
assert.equal(
  useCenterTabs.getState().activeId,
  userSelectedId,
  "failed fallback must preserve a tab selected by the user during activation",
);
useCenterTabs.setState({ tabs: rollbackTabs, activeId: null });
restorePriorActiveTabAfterFailedWebOpen(priorSessionId, null);
assert.equal(
  useCenterTabs.getState().activeId,
  null,
  "a missing fallback web id must not restore the prior tab",
);

const webTabPaneSource = await readFile(
  new URL("../components/center-tabs/web-tab-pane.tsx", import.meta.url),
  "utf8",
);
const appShellSource = await readFile(
  new URL("../components/app-shell.tsx", import.meta.url),
  "utf8",
);
const tabStripSource = await readFile(
  new URL("../components/center-tabs/center-tab-strip.tsx", import.meta.url),
  "utf8",
);
const tabsCssSource = await readFile(
  new URL("../components/center-tabs/center-tabs.module.css", import.meta.url),
  "utf8",
);
const baseCssSource = await readFile(
  new URL("../app/styles/base.css", import.meta.url),
  "utf8",
);
const desktopBridgeSource = await readFile(
  new URL("../lib/desktop-bridge.ts", import.meta.url),
  "utf8",
);

assert.match(desktopBridgeSource, /function visibleWebTab\(\)/);
assert.match(
  desktopBridgeSource,
  /active\?\.kind === "web" && isWebTabReady\(active\.id\)/,
);
assert.match(
  desktopBridgeSource,
  /active\?\.kind === "session" && split\?\.kind === "web"[\s\S]*?isWebTabReady\(split\.id\)/,
);
assert.match(desktopBridgeSource, /state\.openWebTabInSplit\(d\.url\)/);
assert.match(desktopBridgeSource, /waitForWebTabReady\(id, 2000\)/);
assert.match(
  desktopBridgeSource,
  /if \(!split && !routeVisible\) \{\s*const routed = showCenterSurface\(\);\s*if \(!routed\)/,
  "split opens and existing /s or /chat fallbacks must preserve their route",
);
assert.doesNotMatch(
  desktopBridgeSource,
  /openWebTab\(d\.url\);\s*const routed = showCenterSurface\(\);/,
  "webtab.command must not unconditionally navigate a session route to /chat",
);

assert.match(appShellSource, /splitWebTabId/);
assert.match(appShellSource, /splitRatio/);
assert.match(
  appShellSource,
  /const splitAvailable = isSplitLayoutAvailable\(centerBodyWidth\);/,
  "split availability must use the measured center-body width",
);
assert.match(
  appShellSource,
  /const showSplit =\s*isDesktop && activeKind === "session" && !!splitTab && splitAvailable;/,
);
assert.equal(
  appShellSource.match(/<PageShell page="chat" \/>/g)?.length,
  1,
  "the chat shell must remain a mounted singleton",
);
assert.match(
  appShellSource,
  /\{showSplit \? \([\s\S]*?<WebTabPane[\s\S]*?tabId=\{splitTab\.id\}[\s\S]*?url=\{splitTab\.url \?\? ""\}[\s\S]*?\) : null\}/,
);
assert.match(appShellSource, /"center-split-chat"/);
assert.match(appShellSource, /className="center-split-divider"/);
assert.match(appShellSource, /className="center-split-web"/);
assert.match(appShellSource, /setDesktopSplitLayoutAvailable/);
assert.match(appShellSource, /clampSplitRatioForWidth/);
assert.match(appShellSource, /const effectiveSplitRatio = clampSplitRatioForWidth/);
assert.doesNotMatch(
  appShellSource,
  /if \(effectiveSplitRatio !== splitRatio\) setSplitRatio\(effectiveSplitRatio\);/,
  "container constraints must not overwrite the preferred split ratio",
);
assert.match(
  appShellSource,
  /new ResizeObserver\(\(\[entry\]\) =>\s*setCenterBodyWidth\(entry\.contentRect\.width\),?\s*\)/,
  "ResizeObserver must consume the measured center-body width",
);
assert.match(appShellSource, /window\.addEventListener\("resize", update\)/);
assert.match(appShellSource, /window\.removeEventListener\("resize", update\)/);
assert.match(appShellSource, /width: `\$\{effectiveSplitRatio \* 100\}%`/);
assert.match(appShellSource, /aria-valuenow=\{Math\.round\(effectiveSplitRatio \* 100\)\}/);
assert.match(appShellSource, /onPointerDown=/);
assert.match(appShellSource, /onPointerMove=/);
assert.match(appShellSource, /role="separator"/);
assert.match(appShellSource, /aria-orientation="vertical"/);
assert.match(appShellSource, /"ArrowLeft"/);
assert.match(appShellSource, /"ArrowRight"/);
assert.match(appShellSource, /0\.02/);

assert.match(webTabPaneSource, /text\("Open split view", "打开分屏"\)/);
assert.match(webTabPaneSource, /text\("Exit split view", "退出分屏"\)/);
assert.match(webTabPaneSource, /setSplitWebTab\(null\)/);
assert.match(webTabPaneSource, /setRightDockOpen\(false\)/);
assert.match(webTabPaneSource, /openDraftSessionTab\(\)/);
assert.match(webTabPaneSource, /\.newSession\?\.\(draftId\)/);
assert.match(
  webTabPaneSource,
  /const title = sessionState\.conversations\[routeSessionId\]\?\.title \?\? "";/,
);
assert.match(webTabPaneSource, /state\.openSessionTab\(routeSessionId, title\);/);
assert.match(
  webTabPaneSource,
  /if \(openedSession\) openedState\.setActive\(openedSession\.id\);/,
);
assert.match(
  webTabPaneSource,
  /if \(routeSession\) \{[\s\S]*?state\.setActive\(routeSession\.id\);[\s\S]*?\} else if \(routeSessionId\) \{[\s\S]*?state\.openSessionTab\(routeSessionId, title\);[\s\S]*?\} else if \(activeDraft\) \{[\s\S]*?state\.setActive\(activeDraft\.id\);[\s\S]*?\} else \{[\s\S]*?state\.openDraftSessionTab\(\)/,
);

assert.match(tabStripSource, /data-split-pinned=\{splitPinned \|\| undefined\}/);
assert.match(tabStripSource, /active=\{tab\.id === activeId\}/);
assert.match(tabsCssSource, /\[data-split-pinned="true"\]/);
assert.match(baseCssSource, /\.center-split-divider\s*\{[^}]*width:\s*6px;/s);
assert.match(baseCssSource, /\.center-split-chat\s*\{[^}]*min-width:\s*360px;/s);
assert.match(baseCssSource, /\.center-split-web\s*\{[^}]*min-width:\s*480px;/s);
assert.match(webTabPaneSource, /setWebTabReady/);
assert.doesNotMatch(
  webTabPaneSource,
  /bridge\.webTab\.navigate\(tabId, viewUrlRef\.current\);/,
);
assert.match(webTabPaneSource, /const occluded = !!document\.querySelector/);
assert.match(
  webTabPaneSource,
  /\[role="dialog"\], \.branches-merge-modal-backdrop, \[data-native-view-occluder="true"\]/,
);
assert.match(
  webTabPaneSource,
  /const roundedBounds = \{\s*x: Math\.round\(r\.left\),\s*y: Math\.round\(r\.top\),\s*width: Math\.round\(r\.width\),\s*height: Math\.round\(r\.height\),\s*\};/s,
);
assert.match(
  webTabPaneSource,
  /if \(occluded \|\| roundedBounds\.width <= 0 \|\| roundedBounds\.height <= 0\) \{\s*bridge\.webTab\.setBounds\(tabId, \{ x: 0, y: 0, width: 0, height: 0 \}\);\s*setWebTabReady\(tabId, false\);/s,
);
assert.match(
  webTabPaneSource,
  /bridge\.webTab\.setBounds\(tabId, roundedBounds\);\s*setWebTabReady\(tabId, true\);/s,
);
assert.match(
  webTabPaneSource,
  /return \(\) => \{\s*setWebTabReady\(tabId, false\);\s*bridge\.webTab\.hide\(tabId\);/s,
);
assert.match(webTabPaneSource, /new MutationObserver\(report\)/);
assert.match(
  webTabPaneSource,
  /mo\.observe\(document\.body, \{ subtree: true, childList: true, attributes: true \}\);/,
);
assert.match(webTabPaneSource, /mo\.disconnect\(\);/);

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
