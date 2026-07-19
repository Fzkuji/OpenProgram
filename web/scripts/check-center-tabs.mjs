import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const css = readFileSync(
  new URL("../components/center-tabs/center-tabs.module.css", import.meta.url),
  "utf8",
);
const ntp = readFileSync(
  new URL("../components/center-tabs/new-tab-page.tsx", import.meta.url),
  "utf8",
);
const conversations = readFileSync(
  new URL("../lib/runtime-bridge/conversations.ts", import.meta.url),
  "utf8",
);
const strip = readFileSync(
  new URL("../components/center-tabs/center-tab-strip.tsx", import.meta.url),
  "utf8",
);
const appShell = readFileSync(
  new URL("../components/app-shell.tsx", import.meta.url),
  "utf8",
);
const desktopBridge = readFileSync(
  new URL("../lib/desktop-bridge.ts", import.meta.url),
  "utf8",
);
const webTabPane = readFileSync(
  new URL("../components/center-tabs/web-tab-pane.tsx", import.meta.url),
  "utf8",
);
const fileTree = readFileSync(
  new URL("../components/files/file-tree.tsx", import.meta.url),
  "utf8",
);

assert.match(css, /max-width: calc\(100% - 36px\);/);
assert.match(css, /:global\(html\.is-desktop\) \.strip \{[^}]*padding-right: 10px;/s);
assert.match(
  css,
  /:global\(html\.is-desktop\) \.tabsFlow \{[^}]*overflow-x: auto;[^}]*overflow-y: hidden;[^}]*scrollbar-width: none;/s,
);
assert.match(
  css,
  /:global\(html\.is-desktop\) \.tabsFlow::\-webkit-scrollbar \{[^}]*display: none;/s,
);
assert.match(css, /:global\(html\.is-desktop\) \.tabsFlow > \.tab \{[^}]*width: 240px;[^}]*flex: 0 1 240px;[^}]*max-width: 240px;/s);
assert.match(css, /@keyframes desktopTabEnter \{\s*from \{ opacity: 0; \}\s*\}/);
assert.match(css, /:global\(html\.is-desktop\) \.tabEnter \{[^}]*animation: desktopTabEnter 120ms ease-out;/s);
assert.match(css, /:global\(html\.is-desktop\) \.tabExit \{[^}]*animation: tabExit 160ms ease-in forwards;/s);
assert.match(ntp, /const draftId = useCenterTabs\.getState\(\)\.claimDraftSessionTab\(\);[\s\S]*\.newSession\?\.\(draftId\);/);
assert.match(strip, /currentSessionId === null[\s\S]*activeTab\?\.draft/);
assert.match(strip, /closingInstances = useRef<Map<string, CenterTab>>/);
assert.match(strip, /const \[closingIds, setClosingIds\] = useState<Set<string>>/);
assert.match(
  strip,
  /closing=\{closingIds\.has\(tab\.id\)\}/,
);
assert.match(strip, /if \(!currentTab\) return;/);
assert.doesNotMatch(
  strip,
  /\}, \[activeId, currentSessionId, pathname, openSessionTab, openDraftSessionTab\]\);/,
);
assert.match(
  strip,
  /function onTabClick[\s\S]*tab\.kind === "session"[\s\S]*router\.push\("\/chat"\)/,
);
assert.match(strip, /function onOpenNewTab[\s\S]*openNewTabPage\(\)[\s\S]*router\.push\("\/chat"\)/);
assert.match(strip, /onClick=\{onOpenNewTab\}/);
assert.match(
  strip,
  /if \(active\) tabRef\.current\?\.scrollIntoView\(\{ block: "nearest", inline: "nearest" \}\);/,
);
assert.match(strip, /ref=\{tabRef\}/);
assert.match(strip, /const observer = new ResizeObserver\(revealActiveTab\);/);
assert.match(strip, /observer\.observe\(flow\);/);
assert.match(
  strip,
  /strip\.toggleAttribute\("data-plus-rail-aligned", railAligned\);/,
  "the full-width desktop strip must expose when + reaches the right rail",
);
assert.match(
  strip,
  /useLayoutEffect\(\(\) => \{[\s\S]*data-plus-rail-aligned/,
  "desktop rail alignment must be resolved before the first paint",
);
assert.match(strip, /plusRef/);
assert.match(strip, /stripRef/);
assert.match(strip, /tabsFlowRef/);
assert.match(
  css,
  /:global\(html\.is-desktop\) \.strip\[data-plus-rail-aligned\] \.plusBtn::before \{[^}]*left: -11px;[^}]*width: 1px;/s,
  "the pinned + separator must share the 49px rail boundary",
);
assert.match(
  desktopBridge,
  /openNewTabPage\(\);[\s\S]*showCenterSurface\(\);/,
);
assert.match(
  desktopBridge,
  /openWebTab\(d\.url\);[\s\S]*showCenterSurface\(\);/,
);
assert.match(appShell, /showChat && activeKind === "file"/);
assert.match(appShell, /showChat && activeKind === "web"/);
assert.match(appShell, /showChat && activeKind === "ntp"/);
const ensureIndex = webTabPane.indexOf("ensureWebView(bridge, tabId, viewUrlRef.current)");
const navigateIndex = webTabPane.indexOf("bridge.webTab.navigate(tabId, viewUrlRef.current)");
const showIndex = webTabPane.indexOf("bridge.webTab.show(tabId)");
assert.ok(ensureIndex >= 0 && ensureIndex < navigateIndex && navigateIndex < showIndex);
assert.match(fileTree, /openFileTab\(projectId, path\);[\s\S]*__navigate\?\.\("\/chat"\)/);

const newSession = conversations.slice(conversations.indexOf("export function newSession"));
assert.ok(
  newSession.indexOf("W.currentSessionId = null") <
    newSession.indexOf("if (needsNavigation)"),
  "newSession must clear the current session before SPA navigation",
);
assert.ok(
  newSession.indexOf("setCurrentDraft(draftId)") <
    newSession.indexOf("if (needsNavigation)"),
  "newSession must select the distinct React draft before SPA navigation",
);

console.log("center-tabs checks passed");
