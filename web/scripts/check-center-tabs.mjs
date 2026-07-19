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

const dragStart = strip.slice(
  strip.indexOf("function onDragStart"),
  strip.indexOf("function onDragEnd"),
);
assert.ok(dragStart.length > 0, "the strip must define one synchronous drag-start path");
assert.doesNotMatch(strip, /async function\s+\w*DragStart/);
assert.doesNotMatch(dragStart, /\bawait\b/);
assert.match(dragStart, /dragCoordinator\.start\(\)/);
assert.match(dragStart, /const prepared = dragCoordinator\.current\(\)/);
assert.match(dragStart, /dataTransfer\.effectAllowed\s*=\s*"move"/);
assert.match(dragStart, /dataTransfer\.setData\(/);
assert.equal(
  strip.match(/dataTransfer\.setData\(/g)?.length,
  dragStart.match(/dataTransfer\.setData\(/g)?.length,
  "every transfer payload must be written by the synchronous coordinator-backed drag start",
);
assert.ok(
  strip.indexOf("function onPrepareDrag") < strip.indexOf("function onDragStart"),
  "pointer preparation must be defined before native drag start",
);
assert.match(strip, /onPointerDown=\{[^}]*onPrepareDrag/s);
assert.match(strip, /onDragStart=\{onDragStart\}/);
assert.match(strip, /resolveTabDropIntent\(/);
assert.match(strip, /moveGroupMember\(/);
assert.match(strip, /moveGroup\(/);
assert.match(strip, /ungroupTab\(/);
assert.match(strip, /groupTab\(/);
const applyDrop = strip.slice(
  strip.indexOf("function applyDrop"),
  strip.indexOf("function onDragOver"),
);
assert.match(applyDrop, /subject\.kind === "group"[\s\S]*return mergeGroup\(/);
assert.doesNotMatch(applyDrop, /if \(subject\.kind === "group"\) return false;/);
assert.match(strip, /className=\{styles\.groupDragHandle\}[\s\S]*kind: "group"/);
assert.match(strip, /onMoveGroup\(group\.id,/);
assert.match(strip, /window\.addEventListener\("pointerup",[^;]+\{ once: true \}\)/s);
assert.match(strip, /if \(e\.key !== "Escape"\) return;/);

const closeButton = strip.slice(strip.indexOf("<button\n        type=\"button\"", strip.indexOf("function TabItem")), strip.indexOf("<X size={14} />"));
assert.match(closeButton, /draggable=\{false\}/);
assert.match(closeButton, /onPointerDown=\{\(event\) => event\.stopPropagation\(\)\}/);
assert.match(closeButton, /onMouseDown=\{\(event\) => event\.stopPropagation\(\)\}/);
assert.doesNotMatch(closeButton, /onPrepareDrag/);
assert.match(css, /\[data-drag-source="true"\][^{]*\{[^}]*opacity:/s);
assert.match(css, /\[data-drop-intent="before"\]/);
assert.match(css, /\[data-drop-intent="merge"\]/);
assert.match(css, /\[data-drop-intent="after"\]/);
assert.match(
  css,
  /:global\(html\.is-desktop\) \.tab\.tabActive\[data-drop-intent="before"\][^{]*,[\s\S]*\.compoundTabActive\[data-drop-intent="before"\]\s*\{[^}]*box-shadow:\s*inset 3px 0 var\(--accent-blue\),/s,
  "desktop active normal and compound targets must retain the before marker",
);
assert.match(
  css,
  /:global\(html\.is-desktop\) \.tab\.tabActive\[data-drop-intent="after"\][^{]*,[\s\S]*\.compoundTabActive\[data-drop-intent="after"\]\s*\{[^}]*box-shadow:\s*inset -3px 0 var\(--accent-blue\),/s,
  "desktop active normal and compound targets must retain the after marker",
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
assert.match(
  css,
  /:global\(html\.is-desktop\) \.tab \{[^}]*height: 30px;[^}]*padding-right: 5px;[^}]*border-radius: 8px;/s,
  "desktop tab close controls must have the same 5px right gap as their vertical gaps",
);
assert.match(css, /\.tabClose \{[^}]*width: 20px;[^}]*height: 20px;/s);
assert.match(strip, /<X size=\{14\} \/>/);
assert.match(css, /@keyframes desktopTabEnter \{\s*from \{ opacity: 0; \}\s*\}/);
assert.match(css, /:global\(html\.is-desktop\) \.tabEnter \{[^}]*animation: desktopTabEnter 120ms ease-out;/s);
assert.match(css, /\.tabExit \{[^}]*animation: tabExit 160ms ease-in forwards;/s);
assert.match(css, /:global\(html\.is-desktop\) \.tabExit \{[^}]*animation: tabExit 120ms ease-in forwards;/s);
assert.match(strip, /centerTabStripEntries/);
assert.match(strip, /function CompoundTabItem/);
assert.match(
  strip,
  /className=\{`\$\{styles\.compoundTab\} \$\{active \? styles\.compoundTabActive : ""\}`\}/,
);
assert.match(strip, /group\.memberIds\.map\(\(tabId\) =>/);
assert.match(strip, /enteringIds\.has\(tab\.id\)/);
assert.match(strip, /closingIds\.has\(tab\.id\)/);
assert.match(strip, /onAnimationEnd/);
assert.match(strip, /onExited\(tab\)/);
assert.match(strip, /onExited=\{finishClose\}/);
assert.match(strip, /onClose=\{onTabClose\}/);
assert.doesNotMatch(strip, /splitPinned|data-split-pinned/);
assert.doesNotMatch(
  css,
  /data-split-pinned|inset 0 2px 0 var\(--accent-blue\)/,
);
assert.match(
  css,
  /\.compoundTab\[data-member-count="2"\]\s*\{[^}]*width:\s*360px;[^}]*flex:\s*0 1 360px;[^}]*max-width:\s*360px;/s,
);
assert.match(
  css,
  /\.compoundTab\[data-member-count="3"\]\s*\{[^}]*width:\s*440px;[^}]*flex:\s*0 1 440px;[^}]*max-width:\s*440px;/s,
);
assert.match(
  css,
  /\.compoundSegment \+ \.compoundSegment\s*\{[^}]*border-left:\s*1px solid var\(--border\);/s,
);
assert.match(
  strip,
  /const closingCount = group\.memberIds\.filter\(\(tabId\) =>\s*closingIds\.has\(tabId\),\s*\)\.length;/,
  "compound geometry must account for every concurrently closing segment",
);
assert.match(strip, /const remainingCount = group\.memberIds\.length - closingCount;/);
assert.match(strip, /data-closing-count=\{closingCount \|\| undefined\}/);
assert.match(strip, /data-remaining-count=\{remainingCount\}/);
assert.match(
  css,
  /\.compoundTab \{[^}]*transition:\s*width 160ms ease-in,\s*flex-basis 160ms ease-in,\s*max-width 160ms ease-in;/s,
  "the compound outer width must animate for the full browser segment-exit duration",
);
assert.match(
  css,
  /\.compoundTab\[data-closing-count\]\[data-remaining-count="2"\]\s*\{[^}]*width:\s*360px;[^}]*flex-basis:\s*360px;[^}]*max-width:\s*360px;/s,
  "three members closing to two must animate the outer width to 360 DIP",
);
assert.match(
  css,
  /\.compoundTab\[data-closing-count\]\[data-remaining-count="1"\]\s*\{[^}]*width:\s*200px;[^}]*flex-basis:\s*200px;[^}]*max-width:\s*200px;/s,
  "browser two-to-one collapse must end at the ordinary 200 DIP tab width",
);
assert.match(
  css,
  /:global\(html\.is-desktop\) \.compoundTab\[data-closing-count\]\[data-remaining-count="1"\]\s*\{[^}]*width:\s*240px;[^}]*flex-basis:\s*240px;[^}]*max-width:\s*240px;/s,
  "desktop two-to-one collapse must end at the ordinary 240 DIP tab width",
);
assert.match(
  css,
  /:global\(html\.is-desktop\) \.compoundTab\s*\{[^}]*transition-duration:\s*120ms;/s,
  "desktop compound width and segment exit must use the same 120ms duration",
);
assert.match(
  strip,
  /tabRef\.current\?\.closest<HTMLElement>\('\[role="tablist"\]'\)/,
  "compound segments must observe the actual tab flow, not the compound wrapper",
);
assert.doesNotMatch(
  strip.slice(strip.indexOf("function TabItem")),
  /tabRef\.current\?\.parentElement/,
);
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
  /const \[sessionActivationRequest, setSessionActivationRequest\] = useState\(0\);/,
);
assert.match(
  strip,
  /Active center-tab focus[\s\S]*useEffect\(\(\) => \{[\s\S]*activateSession\(tab\);[\s\S]*\}, \[activeId, sessionActivationRequest\]\);/,
);
const onTabClick = strip.slice(
  strip.indexOf("function onTabClick"),
  strip.indexOf("function onOpenNewTab"),
);
assert.match(onTabClick, /tab\.kind === "session" && tab\.id === activeId/);
assert.match(
  onTabClick,
  /setSessionActivationRequest\(\(request\) => request \+ 1\);/,
);
assert.doesNotMatch(onTabClick, /activateSession/);
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
  /\.plusBtn::before \{[^}]*width: 2px;[^}]*border-radius: 1px;[^}]*background: var\(--plus-separator-background, var\(--border\)\);/s,
  "short desktop tab lists must retain the normal 2px rounded divider",
);
assert.match(
  css,
  /:global\(html\.is-desktop\) \.strip\[data-plus-rail-aligned\] \.plusBtn::before \{[^}]*left: -12px;[^}]*width: 2px;[^}]*border-radius: 1px;[^}]*--plus-separator-background:\s*linear-gradient\(var\(--border\), var\(--border\)\),\s*var\(--tabrow-bg\);/s,
  "the pinned + separator must retain the normal tab-divider appearance",
);
assert.match(
  css,
  /\.tabsFlow:has\(> \.tabActive:last-child\) \+ \.plusBtn::before,\s*\.tabsFlow:has\(> \.tab:last-child:hover\) \+ \.plusBtn::before,\s*\.plusBtn:hover::before \{\s*background: transparent;/,
  "active and hovered tabs must still hide the normal + divider",
);
assert.doesNotMatch(
  css,
  /:global\(html\.is-desktop\) \.strip\[data-plus-rail-aligned\] \.plusBtn::before \{[^}]*\n\s*background:/s,
  "the pinned divider must not override the shared active and hover hiding rule",
);
assert.match(
  css,
  /:global\(html\.is-desktop\) \.strip\[data-plus-rail-aligned\] \.plusBtn \{[^}]*z-index: 2;/s,
  "the pinned rail boundary must paint above the active tab",
);
assert.match(
  css,
  /:global\(html\.is-desktop\) \.strip\[data-plus-rail-aligned\] \.tabsFlow \{[^}]*padding-right: 3px;/s,
  "the saturated tab flow must end at the 49px rail boundary",
);
assert.match(
  desktopBridge,
  /openNewTabPage\(\);[\s\S]*showCenterSurface\(\);/,
);
assert.match(
  desktopBridge,
  /openWebTab\(d\.url\);[\s\S]*showCenterSurface\(\);/,
);
assert.match(appShell, /tab\.kind === "file"[\s\S]*?<FileTabPane/);
assert.match(appShell, /tab\.kind === "web"[\s\S]*?<WebTabPane/);
assert.match(appShell, /tab\.kind === "ntp"[\s\S]*?<NewTabPage/);
const ensureIndex = webTabPane.indexOf("ensureWebView(bridge, tabId, viewUrlRef.current)");
const navigateIndex = webTabPane.indexOf("bridge.webTab.navigate(tabId, viewUrlRef.current)");
const registerBoundsIndex = webTabPane.indexOf(
  "registerVisibleWebTabBounds(bridge, tabId, roundedBounds)",
);
assert.ok(ensureIndex >= 0 && ensureIndex < registerBoundsIndex);
assert.equal(navigateIndex, -1);
assert.doesNotMatch(webTabPane, /bridge\.webTab\.(?:show|hide|setBounds)\(/);
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
