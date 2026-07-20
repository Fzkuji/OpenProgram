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

// ---- Pointer-driven drag (no HTML5 drag-and-drop) -------------------
assert.doesNotMatch(strip, /draggable|dataTransfer|onDragStart|onDragOver|onDrop=|onDragEnd|onDragLeave/,
  "the strip's same-window drag must be pointer-driven, not HTML5");
// Pointer down prepares the main-process token synchronously.
const prepareDrag = strip.slice(
  strip.indexOf("function onPrepareDrag"),
  strip.indexOf("// ---- Pointer-driven drag"),
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
  "release before the threshold must cancel the prepared token");
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
const pointerMove = strip.slice(
  strip.indexOf("function onPointerDragMove"),
  strip.indexOf("function onPointerDragUp"),
);
// 4px threshold before the press becomes a drag; then the coordinator starts.
assert.match(pointerMove, /Math\.hypot\(dx, dy\) < DRAG_START_THRESHOLD_PX\) return;/);
assert.match(pointerMove, /dragCoordinator\.start\(\)/);
// The tab element itself follows the pointer, clamped to the slot span.
assert.match(pointerMove, /drag\.element\.style\.transform = `translateX\(\$\{tx\}px\)`/);
assert.match(pointerMove, /Math\.min\(Math\.max\(dx, drag\.minTx\), drag\.maxTx\)/);
// The clamp must bound the dragged tab's CENTER against the slot span,
// not its BODY against the flow: clamping the body leaves the outermost
// edge quarters unreachable, so the first/last tab could never be merged
// into (the center stops half a tab short of the far edge).
assert.match(pointerMove, /const center0 = unitRect\.left \+ unitRect\.width \/ 2;/);
assert.match(pointerMove, /drag\.minTx = firstSlot \? firstSlot\.left - center0 : -Infinity;/);
assert.match(pointerMove, /lastSlot\.left \+ lastSlot\.width - center0/);
assert.doesNotMatch(
  pointerMove,
  /flowRect\.right - unitRect\.right/,
  "clamping the tab body to the flow makes the edge merge zones unreachable",
);
// Pointer capture lives on the dragged tab element.
assert.match(pointerMove, /drag\.element\.setPointerCapture\(drag\.pointerId\)/);
assert.match(strip, /releasePointerCapture\(/);
// Chrome midpoint reorder against STATIC slot geometry captured at drag
// start — bystanders slide via transform, hit tests never see it.
assert.match(pointerMove, /collectPointerDropTargets\(flow\)/);
assert.match(pointerMove, /const centerX = drag\.originLeft \+ tx \+ drag\.width \/ 2;/);
assert.match(pointerMove, /pickPointerDropTarget\(drag\.targets, centerX\)/);
assert.match(pointerMove, /resolveTabDropIntent\(target, centerX, target\)/);
// Merge is fixed slot geometry — both edge quarters, no direction, no
// dwell — and the drag's own tabs never merge into themselves.
assert.match(
  pointerMove,
  /!drag\.selfIds\.has\(target\.tabId\) && isInMergeZone\(target, centerX\)/,
);
assert.doesNotMatch(
  pointerMove,
  /drag\.direction|drag\.lastX|drag\.lastTx/,
  "the merge test must not depend on travel direction",
);
// The tab merge decision itself is a straight positional test: from
// isInMergeZone to publishing the merge there is no timer at all.
// (MERGE_DWELL_MS survives only for the pane-area dwell, above it.)
const tabMergeDecision = pointerMove.slice(pointerMove.indexOf("isInMergeZone"));
assert.doesNotMatch(
  tabMergeDecision,
  /setTimeout|DWELL/,
  "tab merge must be positional — no dwell timer",
);
// The only surviving dwell is the center-pane merge surface.
assert.match(pointerMove, /PANE_MERGE_DWELL_MS/);
assert.doesNotMatch(strip, /dwellRef|clearDwell/, "the tab dwell machinery is gone");
// Detach: DETACH_DISTANCE_PX vertical travel with a live desktop token.
assert.match(pointerMove, /Math\.abs\(dy\) > DETACH_DISTANCE_PX/);
assert.match(pointerMove, /data-detach-intent/);
const pointerUp = strip.slice(
  strip.indexOf("function onPointerDragUp"),
  strip.indexOf("function targetBeforeId"),
);
// Release off-window: another OpenProgram window under the cursor takes
// the tab; otherwise detach into a new window.
assert.match(pointerUp, /tabTransfer\.windowAtCursor/);
assert.match(pointerUp, /tabTransfer\.deliver/);
assert.match(pointerUp, /tabTransfer\.detach\(token\)/,
  "a detach release with a live token must detach into a new window");
assert.ok(
  pointerUp.indexOf("windowAtCursor") < pointerUp.indexOf("tabTransfer.detach(token)"),
  "the cross-window hit test must run before falling back to detach",
);
// In-strip release commits the live intent — a dwell merge wins.
assert.match(pointerUp, /const intent = drag\.merge \?\? drag\.lastIntent;/);
assert.match(pointerUp, /tabTransfer\.cancel\(committed\.transferToken\)/,
  "a same-window drop must release the unused prepared token");
assert.match(pointerUp, /const fourthMemberRejected = isFourthMemberRejection\(/);
assert.match(pointerUp, /cancelDrag\(!fourthMemberRejected\)/);
// Pane merge on release after the pane dwell armed.
assert.match(pointerUp, /mergeSubjectIntoTab\(prepared\.subject, targetId\)/);
assert.match(pointerMove, /paneMergeSurfaceContains\(e\.clientX, e\.clientY\)/);
assert.match(pointerMove, /setPaneMergeHighlight\(true\)/);
// Cancel paths: pointercancel, window blur, Escape — return-home + cleanup.
assert.match(strip, /window\.addEventListener\("pointercancel", cancel\);/);
assert.match(strip, /window\.addEventListener\("blur", cancel\);/);
assert.match(strip, /function cancelPointerDrag/);
assert.match(strip, /function teardownPointerDrag/);
assert.match(strip, /if \(e\.key !== "Escape"\) return;[\s\S]*?teardownPointerDrag\(\)/);
assert.match(strip, /onPointerDown=\{\(event\) => onDragPointerDown\(dragSubject, event\)\}/);
assert.match(strip, /moveGroupMember\(/);
assert.match(strip, /moveGroup\(/);
assert.match(strip, /ungroupTab\(/);
assert.match(strip, /groupTab\(/);
const applyDrop = strip.slice(
  strip.indexOf("function applyDrop"),
  strip.indexOf("function moveGroupByKeyboard"),
);
assert.match(applyDrop, /subject\.kind === "group"[\s\S]*return mergeGroup\(/);
assert.doesNotMatch(applyDrop, /if \(subject\.kind === "group"\) return false;/);
assert.match(strip, /className=\{styles\.groupDragHandle\}[\s\S]*kind: "group"/);
assert.match(strip, /onMoveGroup\(group\.id,/);
assert.match(strip, /window\.addEventListener\("pointerup", cancelUnstarted, \{ once: true \}\)/);
assert.match(strip, /function isFourthMemberRejection/);

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
  "Tabs merged into split view",
]) {
  assert.match(strip, new RegExp(announcement));
}
assert.match(strip, /function returnFocusToMenuInvoker/);
assert.match(strip, /if \(e\.key !== "Escape"\) return;[\s\S]*cancelCoordinator\(\)[\s\S]*setDropMarker\(null\)[\s\S]*setTabMenu\(null\)/);

const closeButton = strip.slice(strip.indexOf("<button\n        type=\"button\"", strip.indexOf("function TabItem")), strip.indexOf("<X size={14} />"));
assert.match(closeButton, /onPointerDown=\{\(event\) => event\.stopPropagation\(\)\}/);
assert.match(closeButton, /onMouseDown=\{\(event\) => event\.stopPropagation\(\)\}/);
assert.doesNotMatch(closeButton, /onDragPointerDown/);
// The dragged tab body follows the pointer: transitions off, raised
// above sliding bystanders, and FULLY OPAQUE over an opaque background
// (.tab is background:transparent by default — without a fill the
// dragged tab shows the tabs underneath through itself).
const pointerDragRule = css.slice(
  css.indexOf('.tab[data-pointer-drag="true"]'),
  css.indexOf('.tab[data-pointer-drag="true"]::before'),
);
assert.match(pointerDragRule, /transition: none;/);
assert.match(pointerDragRule, /z-index: 30;/);
assert.match(pointerDragRule, /opacity: 1;/, "the follow-the-pointer tab must be fully opaque");
assert.match(
  pointerDragRule,
  /background: var\(--bg-primary\);/,
  "the dragged tab needs an opaque fill so neighbours cannot show through",
);
// Translucency is allowed ONLY in the detach state, and it lifts above
// the strip there so it still cannot overlap neighbours' content.
const detachRule = css.slice(
  css.indexOf('.tab[data-detach-intent="true"]'),
  css.indexOf('data-drop-intent="merge"'),
);
assert.match(detachRule, /opacity: 0\.7;/);
assert.match(detachRule, /z-index: 40;/, "the detach state must lift clear of the strip");
assert.doesNotMatch(css, /data-drag-source/);
// ---- Live reorder (Chrome-style slide-aside, no insert markers) ----
assert.doesNotMatch(
  css,
  /data-drop-intent="(?:before|after)"/,
  "before/after insert markers are gone — bystanders slide aside instead",
);
assert.match(
  css,
  /\.tab\[data-drop-intent="merge"\]\s*\{[^}]*outline: 2px solid var\(--accent-blue\);/s,
  "merge targets must keep their highlight",
);
assert.match(css, /\.tab \{[^}]*transition: transform 160ms ease;/s,
  "tabs must transition transform for the slide-aside reorder");
assert.match(
  css,
  /\.compoundTab \{[^}]*transition:[^}]*transform 160ms ease;/s,
  "compound tabs must transition transform for the slide-aside reorder",
);
assert.match(
  css,
  /@media \(prefers-reduced-motion: reduce\) \{\s*\.tab,\s*\.compoundTab \{\s*transition-duration: 0ms;/s,
);
assert.match(strip, /function computeLiveShifts\(/);
assert.match(
  strip,
  /marker\.mode === "merge"[^)]*\) return shifts;/,
  "merge intents must not shift bystanders — highlight and slide are exclusive",
);
assert.match(
  strip,
  /translateX\(\$\{shiftX\}px\)/,
  "shifted entries must move via transform, never layout",
);
assert.match(
  strip,
  /Static slot geometry captured at drag start/,
  "drop intent math must use untransformed slot geometry",
);
assert.match(
  strip,
  /dropMarker\.mode === "merge"\s*\? "merge"\s*: undefined/,
  "plain tabs must only surface the merge intent",
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
  css,
  /:global\(html\.is-desktop\) \.compoundSegment,\s*:global\(html\.is-desktop\) \.compoundTab \.compoundSegment\.tabActive,\s*:global\(html\.is-desktop\) \.compoundTab \.compoundSegment\.tabActive:hover \{[^}]*border-radius: 0;[^}]*box-shadow: none;/s,
  "desktop segments must stay flat partitions — no nested capsule radius or shadow",
);
assert.match(
  strip,
  /const internalSegmentDrag = !groupDragged\s*&& group\.memberIds\.some\(\(tabId\) => draggedIds\.has\(tabId\)\);/,
  "internal segment drags must be detected so the compound FLIP owns the feedback",
);
assert.match(
  strip,
  /previousLeft - child\.offsetLeft[\s\S]*duration: 180, easing: "ease"/,
  "compound member reorders must animate via FLIP position swap",
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
  /\.compoundTab \{[^}]*transition:\s*width 160ms ease-in,\s*flex-basis 160ms ease-in,\s*max-width 160ms ease-in,\s*transform 160ms ease;/s,
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

// ---- Pane-area dwell merge (dwell a pointer-dragged tab over the
// page to merge with the active tab) ----------------------------------
const paneDrop = readFileSync(
  new URL("../components/center-tabs/pane-drop-merge.tsx", import.meta.url),
  "utf8",
);
// No HTML5 drag handlers left — the strip's pointer engine drives it.
assert.doesNotMatch(paneDrop, /onDragOver|onDrop|dataTransfer/);
// Same-window merges reuse the store's strip-equivalent paths.
assert.match(paneDrop, /state\.groupTab\(sourceId, targetId, 1, targetGroup\?\.id\)/);
assert.match(paneDrop, /state\.mergeGroup\(subject\.sourceGroup\.id, targetId, 1\)/);
assert.match(paneDrop, /MAX_CENTER_TAB_GROUP_MEMBERS/);
// Surface registry the strip hit-tests + highlights through.
assert.match(paneDrop, /export function paneMergeSurfaceContains/);
assert.match(paneDrop, /export function setPaneMergeHighlight/);
assert.match(paneDrop, /pane-drop-merge-overlay/);
assert.match(paneDrop, /aria-hidden="true"/);
// The strip commits the merge and announces it (single aria-live).
const stripPaneUp = strip.slice(strip.indexOf("function onPointerDragUp"));
assert.match(stripPaneUp, /mergeSubjectIntoTab\(prepared\.subject, targetId\)/);
assert.match(stripPaneUp, /committed\?\.transferToken/);
// App shell registers the surface + renders the overlay in the pane.
assert.match(appShell, /const paneDropMerge = usePaneDropMerge\(\);/);
const centerBody = appShell.slice(
  appShell.indexOf("paneDropMerge.surfaceRef(node)"),
  appShell.indexOf("<PageShell"),
);
assert.match(centerBody, /\{paneDropMerge\.overlay\}/);
assert.match(
  centerBody,
  /position: "relative"/,
  "overlay is absolutely positioned against the center body",
);

console.log("center-tabs checks passed");
