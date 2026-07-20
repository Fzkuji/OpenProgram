import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";

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
// The clamp must keep the dragged tab's BODY inside the strip's VISIBLE
// span, so it is never clipped by the window edge or dragged over the
// window controls. Bounding the centre against the slot span (an earlier
// merge-era relaxation) let half the tab leave the bar — never again.
assert.match(pointerMove, /const bounds = visibleStripBounds\(flow, stripRef\.current\);/);
assert.match(pointerMove, /drag\.minTx = bounds \? bounds\.left - unitRect\.left : -Infinity;/);
assert.match(pointerMove, /bounds\.right - unitRect\.right/);
assert.doesNotMatch(
  pointerMove,
  /const center0 = unitRect\.left \+ unitRect\.width \/ 2;|firstSlot\.left - center0|lastSlot\.left \+ lastSlot\.width - center0/,
  "the clamp must not bound the tab centre against the slot span",
);
// Visible span, not scrolled content width: the flow scrolls horizontally.
assert.match(
  strip,
  /function visibleStripBounds/,
  "the clamp needs the visible strip span",
);
assert.match(strip, /right: rect\.left \+ flow\.clientWidth/,
  "desktop bound is the flow's client box, not its scrollWidth");
// Browser mode has no flow box (display:contents) — fall back to the
// strip's padded content box so the clamp still works there.
assert.match(strip, /flow\.getClientRects\(\)\.length > 0/);
assert.match(strip, /paddingLeft/);
assert.match(strip, /paddingRight/);
// Pointer capture lives on the dragged tab element.
assert.match(pointerMove, /drag\.element\.setPointerCapture\(drag\.pointerId\)/);
assert.match(strip, /releasePointerCapture\(/);
// Chrome midpoint reorder against STATIC slot geometry captured at drag
// start — bystanders slide via transform, hit tests never see it.
assert.match(pointerMove, /collectPointerDropTargets\(flow\)/);
// Reorder swaps on OVERLAP, not on the dragged centre crossing a
// midpoint: a neighbour yields once the dragged tab covers half of it.
assert.match(pointerMove, /const draggedRect = \{ left: drag\.originLeft \+ tx, width: drag\.width \};/);
assert.match(
  pointerMove,
  /slotOverlapRatio\(drag\.targets\[i\], draggedRect\) < SWAP_OVERLAP_RATIO/,
  "a neighbour must yield at the overlap threshold",
);
assert.match(strip, /function slotOverlapRatio/);
assert.match(strip, /overlap \/ slot\.width/, "overlap is measured against the NEIGHBOUR's width");
// Both directions walk outward from the dragged tab's own slot, so a fast
// flick can cross several neighbours in one move.
assert.match(pointerMove, /for \(let i = selfIndex \+ 1; i < drag\.targets\.length; i\+\+\)/);
assert.match(pointerMove, /for \(let i = selfIndex - 1; i >= 0; i--\)/);
// Cross-group drags (no slot of their own) still resolve via midpoint.
assert.match(pointerMove, /pickPointerDropTarget\(drag\.targets, centerX\)/);
assert.match(pointerMove, /resolveTabDropIntent\(target, centerX, target\)/);
// Dragging in the strip is PURE REORDER — Chrome's model. Splitting is an
// explicit context-menu action, so no merge may be produced by a drag.
assert.doesNotMatch(
  pointerMove,
  /mergeCoverage|MERGE_COVERAGE_THRESHOLD|isInMergeZone|mode: "merge"/,
  "dragging must never produce a merge",
);
assert.doesNotMatch(
  strip,
  /paneMergeSurfaceContains|setPaneMergeHighlight|mergeSubjectIntoTab|PANE_MERGE_DWELL_MS/,
  "the drag-to-pane merge path is gone",
);
assert.doesNotMatch(
  strip,
  /data-drop-intent/,
  "there is no merge highlight during a drag",
);
assert.doesNotMatch(css, /data-drop-intent/, "the merge highlight style is gone");
assert.doesNotMatch(
  pointerMove,
  /drag\.direction|drag\.lastX|drag\.lastTx/,
  "reorder must not depend on travel direction",
);
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
assert.match(pointerUp, /const intent = drag\.lastIntent;/);
assert.match(pointerUp, /tabTransfer\.cancel\(committed\.transferToken\)/,
  "a same-window drop must release the unused prepared token");
assert.match(pointerUp, /const fourthMemberRejected = isFourthMemberRejection\(/);
assert.match(pointerUp, /cancelDrag\(!fourthMemberRejected\)/);
// Cancel paths: pointercancel, window blur, Escape — return-home + cleanup.
assert.match(strip, /window\.addEventListener\("pointercancel", cancel\);/);
assert.match(strip, /window\.addEventListener\("blur", cancel\);/);
assert.match(strip, /function cancelPointerDrag/);
assert.match(strip, /function teardownPointerDrag/);
assert.match(strip, /if \(e\.key !== "Escape"\) return;[\s\S]*?teardownPointerDrag\(\)/);
// Escape must be captured on document: the open menu's buttons hold
// focus, and a focused native web view can swallow window-level keydown.
assert.match(
  strip,
  /document\.addEventListener\("keydown", onEscape, true\)/,
  "Escape must be captured on document, not window",
);
assert.match(strip, /document\.removeEventListener\("keydown", onEscape, true\)/);

// ---- Tab context menu dismissal --------------------------------------
// The menu must close on any outside interaction. Capture phase, so a
// tab's own pointerdown handler cannot consume the dismissal first.
assert.match(
  strip,
  /document\.addEventListener\("pointerdown", onOutsidePointerDown, true\)/,
  "outside pointerdown must dismiss the tab menu",
);
assert.match(
  strip,
  /if \(menu && e\.target instanceof Node && menu\.contains\(e\.target\)\) return;/,
  "clicks inside the menu must not dismiss it",
);
// Blur / scroll / resize close it too — the menu is fixed-positioned and
// would otherwise drift away from the tab it is anchored to.
assert.match(strip, /window\.addEventListener\("blur", dismiss\)/);
assert.match(strip, /window\.addEventListener\("scroll", dismiss, true\)/);
assert.match(strip, /window\.addEventListener\("resize", dismiss\)/);
// Every menu action closes the menu.
assert.match(strip, /function finishMenuAction[\s\S]*?setTabMenu\(null\)/);
assert.match(strip, /function moveMenuTabToNewWindow[\s\S]*?setTabMenu\(null\)/);
// While the menu is open a tab press must NOT arm a drag — the click is
// a dismissal and has to reach the outside-click listener untouched.
assert.match(
  strip,
  /if \(tabMenuRef\.current\) return;/,
  "an open context menu must suppress drag preparation",
);
assert.match(strip, /tabMenuRef\.current = tabMenu;/, "the ref must track the menu state");
// Right/middle button never starts a drag.
assert.match(strip, /if \(event\.button !== 0 \|\| pointerDragRef\.current\) return;/);
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
// ---- Split view picker (Chrome's "New Split View with Current Tab") ---
// The menu entry opens a picker instead of silently pairing with the
// active tab; the picker commits through the same groupTab store action.
assert.match(strip, /function canOpenSplitPicker/);
assert.match(strip, /function openSplitPicker/);
assert.match(strip, /New split view with this tab/);
assert.match(strip, /与此标签页新建分屏/);
assert.match(
  strip,
  /disabled=\{!canOpenSplitPicker\(tabMenu\.tabId\)\}/,
  "the split entry is enabled whenever another tab exists",
);
assert.match(strip, /<SplitViewPicker/);
assert.match(strip, /subjectId=\{splitPickerTabId\}/);
const picker = readFileSync(
  new URL("../components/center-tabs/split-view-picker.tsx", import.meta.url),
  "utf8",
);
// The picker lists other tabs and commits via groupTab.
assert.match(picker, /Choose a tab to add to split view/);
assert.match(picker, /选择要加入分屏的标签页/);
assert.match(picker, /groupTab\(tab\.id, subjectId, memberIndex, subjectGroup\?\.id\)/);
// Candidate filtering lives in the shared layout module (behaviourally
// covered by check-compound-tabs) and is reused, not reimplemented.
assert.match(picker, /splitCandidates\(tabs, groups, subjectId\)/);
// Keyboard: arrow keys move, Enter activates (native button), Esc closes.
assert.match(picker, /e\.key !== "ArrowDown" && e\.key !== "ArrowUp"/);
assert.match(picker, /if \(e\.key === "Escape"\)/);
// Outside pointerdown closes, capture phase like the tab menu.
assert.match(picker, /document\.addEventListener\("pointerdown", onOutside, true\)/);
// A11y: dialog + listbox/option roles, and a close control.
assert.match(picker, /role="dialog"/);
assert.match(picker, /role="listbox"/);
assert.match(picker, /role="option"/);
assert.match(picker, /aria-label=\{text\("Close", "关闭"\)\}/);
// Icons follow project policy: lucide, never emoji.
assert.doesNotMatch(picker, /[\u{1F300}-\u{1FAFF}]/u, "no emoji in the picker");
// The drag-to-merge module is gone entirely.
assert.doesNotMatch(appShell, /usePaneDropMerge|paneDropMerge/);
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
// The split picker reuses the strip's overlay language (same border /
// radius / shadow vocabulary as the tab menu), not a bespoke look.
assert.match(css, /\.splitPicker \{[^}]*border: 1px solid var\(--border\);/s);
assert.match(css, /\.splitPickerOption \{/);
assert.match(css, /\.splitPickerOption:hover,\s*\.splitPickerOption:focus-visible \{/s);
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
assert.doesNotMatch(
  strip,
  /dropIntent/,
  "tabs no longer surface any merge intent — dragging only reorders",
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

// ---- Drag-to-merge is gone entirely -------------------------------
// Splitting is an explicit context-menu action (see the split picker
// assertions above); no module may reintroduce a drag-merge surface.
assert.equal(
  existsSync(new URL("../components/center-tabs/pane-drop-merge.tsx", import.meta.url)),
  false,
  "the drag-to-pane merge module must stay deleted",
);
// The center body still hosts the split picker portal.
assert.match(appShell, /className="center-body"/);
const centerBody = appShell.slice(
  appShell.indexOf('className="center-body"'),
  appShell.indexOf("<PageShell"),
);
assert.match(
  centerBody,
  /position: "relative"/,
  "the split picker is absolutely positioned against the center body",
);

console.log("center-tabs checks passed");
