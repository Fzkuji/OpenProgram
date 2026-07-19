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
// Cross-window transfer wiring: pointer down prepares the main-process
// token synchronously; dragstart only reads it into the DataTransfer.
const prepareDrag = strip.slice(
  strip.indexOf("function onPrepareDrag"),
  strip.indexOf("function onDragStart"),
);
assert.match(prepareDrag, /buildTransferPayload\(snapshot, bridge\.windowId\)/);
assert.match(prepareDrag, /bridge\.tabTransfer\.prepare\(payload\)/);
assert.doesNotMatch(prepareDrag, /\bawait\b/, "pointer-down preparation must stay synchronous");
assert.match(prepareDrag, /transferToken,/, "the prepared token must live in the shared coordinator record");
assert.ok(
  prepareDrag.indexOf("tabTransfer.prepare") < prepareDrag.indexOf("dragCoordinator.prepare"),
  "the token must exist before the coordinator record that carries it",
);
assert.match(prepareDrag, /if \(prepared && !prepared\.started\) cancelCoordinator\(\);/,
  "release before dragstart must cancel the prepared token");
assert.match(dragStart, /setData\(TAB_TRANSFER_MIME, prepared\.transferToken\)/);
assert.match(
  strip,
  /function cancelCoordinator\(\)[\s\S]*?tabTransfer\.cancel\(cancelled\.transferToken\)/,
  "every coordinator cancellation must release its main-process token",
);
assert.equal(
  strip.split("dragCoordinator.cancel()").length - 1,
  1,
  "cancelCoordinator must be the strip's only direct coordinator cancellation",
);
const dragEnd = strip.slice(
  strip.indexOf("function onDragEnd"),
  strip.indexOf("function targetBeforeId"),
);
assert.match(dragEnd, /dropEffect === "none"/);
assert.match(dragEnd, /tabTransfer\.detach\(token\)/,
  "an unhandled drag with a live token must detach into a new window");
assert.ok(
  dragEnd.indexOf('dropEffect === "none"') < dragEnd.indexOf("cancelDrag("),
  "detach must be decided before falling back to cancellation",
);
const drop = strip.slice(
  strip.indexOf("function onDrop"),
  strip.indexOf("function moveGroupByKeyboard"),
);
assert.match(drop, /stageIncomingTransfer\(bridge, token, placementForDropIntent\(intent\)\)/,
  "cross-window drops must stage through the shared placement geometry");
assert.match(drop, /getData\(TAB_TRANSFER_MIME\)/);
assert.match(drop, /tabTransfer\.cancel\(committed\.transferToken\)/,
  "a same-window drop must release the unused prepared token");
assert.doesNotMatch(strip, /dragRef/, "the shared coordinator is the only drag state holder");
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
assert.match(dragEnd, /cancelDrag\(Boolean\(dragCoordinator\.current\(\)\?\.started\)\)/);
assert.match(strip, /function isFourthMemberRejection/);
assert.match(drop, /const fourthMemberRejected = isFourthMemberRejection\(/);
assert.match(drop, /cancelDrag\(!fourthMemberRejected\)/);

const rovingFocus = strip.slice(
  strip.indexOf("function onTabListKeyDown"),
  strip.indexOf("function onTabListWheel"),
);
assert.match(rovingFocus, /"ArrowLeft"/);
assert.match(rovingFocus, /"ArrowRight"/);
assert.match(rovingFocus, /"Home"/);
assert.match(rovingFocus, /"End"/);
assert.match(rovingFocus, /items\[nextIndex\]\.focus\(\)/);
assert.doesNotMatch(rovingFocus, /items\[nextIndex\]\.click\(\)/);
assert.match(strip, /e\.shiftKey && e\.key === "F10"/);
assert.match(strip, /role="menu"/);
assert.match(strip, /role="menuitem"/);
assert.match(strip, /function moveMenuTab/);
const moveMenuTab = strip.slice(
  strip.indexOf("function moveMenuTab"),
  strip.indexOf("function addMenuTabToSplit"),
);
assert.match(moveMenuTab, /moveGroupMember\(/);
assert.match(moveMenuTab, /ungroupTab\(/);
assert.match(moveMenuTab, /moveTab\(/);
assert.match(strip, /function addMenuTabToSplit[\s\S]*groupTab\(/);
assert.match(strip, /function removeMenuTabFromGroup[\s\S]*ungroupTab\(/);
assert.match(strip, /function moveMenuTabToNewWindow/);
const moveToNewWindow = strip.slice(
  strip.indexOf("function moveMenuTabToNewWindow"),
  strip.indexOf("function onOpenNewTab"),
);
assert.match(moveToNewWindow, /buildTransferPayload\(subject, bridge\.windowId\)/,
  "menu move must prepare synchronously through the desktop bridge");
assert.match(moveToNewWindow, /tabTransfer\.prepare\(payload\)/);
assert.match(moveToNewWindow, /tabTransfer\.detach\(token\)/,
  "menu move must detach the prepared token into a new window");
assert.match(moveToNewWindow, /dragCoordinator\.prepare\(/);
assert.match(moveToNewWindow, /dragCoordinator\.start\(\)/);
assert.match(strip, /const canMoveToNewWindow = Boolean\(/);
assert.match(strip, /disabled=\{!canMoveToNewWindow\}/);
assert.match(strip, /role="status"[\s\S]*aria-live="polite"/);
for (const announcement of [
  "Tab reordered",
  "Tab added to split",
  "Tab removed from group",
  "Split supports up to three tabs",
  "Tab move cancelled",
  "Tab moved to new window",
]) {
  assert.match(strip, new RegExp(announcement));
}
assert.match(strip, /function returnFocusToMenuInvoker/);
assert.match(strip, /if \(e\.key !== "Escape"\) return;[\s\S]*cancelCoordinator\(\)[\s\S]*setDropMarker\(null\)[\s\S]*setTabMenu\(null\)/);

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

// ---- Pane-area drop merge (drop a tab onto the page to merge with
// the active tab) -----------------------------------------------------
const paneDrop = readFileSync(
  new URL("../components/center-tabs/pane-drop-merge.tsx", import.meta.url),
  "utf8",
);
// Only our own drags are handled — external file drops fall through.
assert.match(paneDrop, /if \(!isTabDrag\(e\)\) return;/);
assert.match(
  paneDrop,
  /types\.includes\(TAB_TRANSFER_MIME\)/,
  "cross-window drags are recognised by the transfer MIME",
);
assert.match(
  paneDrop,
  /application\/x-openprogram-tab-transfer/,
  "pane drop must use the same transfer MIME as the strip",
);
// Same-window merges reuse the store's strip-equivalent paths.
assert.match(paneDrop, /state\.groupTab\(sourceId, targetId, 1, targetGroup\?\.id\)/);
assert.match(paneDrop, /state\.mergeGroup\(subject\.sourceGroup\.id, targetId, 1\)/);
assert.match(paneDrop, /MAX_CENTER_TAB_GROUP_MEMBERS/);
// Same-window commit releases the unused main-process token; rejects cancel it.
assert.match(paneDrop, /committed\?\.transferToken/);
assert.match(paneDrop, /cancelled\?\.transferToken/);
// Cross-window drops stage a merge onto the active tab.
assert.match(
  paneDrop,
  /stageIncomingTransfer\(\s*bridge,\s*token,\s*placementForDropIntent\(\{ mode: "merge", targetTabId: targetId \}\),?\s*\)/,
);
// A11y: highlight overlay is decorative; announcements go via aria-live.
assert.match(paneDrop, /aria-live="polite"/);
assert.match(paneDrop, /pane-drop-merge-overlay/);
assert.match(paneDrop, /aria-hidden="true"/);
// App shell wires the handlers + overlay onto the center pane container.
assert.match(appShell, /const paneDropMerge = usePaneDropMerge\(\);/);
const centerBody = appShell.slice(
  appShell.indexOf('className="center-body"'),
  appShell.indexOf("<PageShell"),
);
assert.match(centerBody, /onDragOver=\{paneDropMerge\.onDragOver\}/);
assert.match(centerBody, /onDragLeave=\{paneDropMerge\.onDragLeave\}/);
assert.match(centerBody, /onDrop=\{paneDropMerge\.onDrop\}/);
assert.match(centerBody, /\{paneDropMerge\.overlay\}/);
assert.match(
  centerBody,
  /position: "relative"/,
  "overlay is absolutely positioned against the center body",
);

console.log("center-tabs checks passed");
