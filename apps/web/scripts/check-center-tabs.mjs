import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import postcss from "postcss";

import { readCenterTabStripSource } from "./center-tab-strip-source.mjs";
import { readChatCss } from "./_chat-css.mjs";

const css = readFileSync(
  new URL("../components/center-tabs/center-tabs.module.css", import.meta.url),
  "utf8",
);
const cssRoot = postcss.parse(css);
function finalDeclaration(selector, property) {
  let value;
  cssRoot.walkRules((rule) => {
    if (!rule.selectors?.includes(selector)) return;
    rule.walkDecls(property, (declaration) => { value = declaration.value; });
  });
  return value;
}
const ntp = readFileSync(
  new URL("../components/center-tabs/new-tab-page.tsx", import.meta.url),
  "utf8",
);
const conversations = readFileSync(
  new URL("../lib/runtime-bridge/conversations.ts", import.meta.url),
  "utf8",
);
// The strip is split across center-tab-strip.tsx and its submodules
// (drag engine / context menu / lifecycle / drop actions / tab items);
// readCenterTabStripSource concatenates them in source order so every
// assertion below reads the strip as one text, exactly as before.
const strip = readCenterTabStripSource(import.meta.url);
// Pure strip geometry (STRIP_GAP, computeLiveShifts, shiftStyle,
// visibleStripBounds, slotOverlapRatio, collectPointerDropTargets,
// pickPointerDropTarget) now lives in its own module; assertions about
// those DEFINITIONS read here, while the strip's CALL sites stay above.
const geometry = readFileSync(
  new URL("../components/center-tabs/tab-strip-geometry.ts", import.meta.url),
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
const desktopMain = readFileSync(
  new URL("../../desktop/main.js", import.meta.url),
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
assert.match(
  pointerMove,
  /drag\.started = true;[\s\S]*?activatedOnPressRef\.current = activationTabId\(drag\.subject\);/,
  "a completed drag must consume its synthetic follow-up click",
);
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
  geometry,
  /function visibleStripBounds/,
  "the clamp needs the visible strip span",
);
assert.match(geometry, /right: rect\.left \+ flow\.clientWidth/,
  "desktop bound is the flow's client box, not its scrollWidth");
// Browser mode has no flow box (display:contents) — fall back to the
// strip's padded content box so the clamp still works there.
assert.match(geometry, /flow\.getClientRects\(\)\.length > 0/);
assert.match(geometry, /paddingLeft/);
assert.match(geometry, /paddingRight/);
// Pointer capture lives on the dragged tab element.
assert.match(pointerMove, /drag\.element\.setPointerCapture\(drag\.pointerId\)/);
assert.match(strip, /releasePointerCapture\(/);
// Chrome midpoint reorder against STATIC slot geometry captured at drag
// start — bystanders slide via transform, hit tests never see it.
assert.match(pointerMove, /collectPointerDropTargets\(flow\)/);
// Reorder swaps on OVERLAP, not on the dragged centre crossing a
// midpoint: a neighbour yields once the dragged tab covers half of it.
assert.match(pointerMove, /const draggedRect = \{ left: drag\.originLeft \+ drag\.lastTx, width: drag\.width \};/);
assert.match(
  pointerMove,
  /slotOverlapRatio\(drag\.targets\[i\], draggedRect\) >= SWAP_OVERLAP_RATIO/,
  "a neighbour must yield at the overlap threshold",
);
assert.match(geometry, /function slotOverlapRatio/);
assert.match(geometry, /overlap \/ slot\.width/, "overlap is measured against the NEIGHBOUR's width");
// Scan from the FAR end inwards on each side and take the first covered
// slot, so every tab between source and target is included in the shift.
// An early `break` on the first UNcovered neighbour would pin the marker
// to the nearest tab and leave the ones beyond it un-shifted.
assert.match(
  pointerMove,
  /for \(let i = drag\.targets\.length - 1; i > selfIndex; i--\)/,
  "the rightward scan must start at the far end",
);
assert.match(
  pointerMove,
  /for \(let i = 0; i < selfIndex; i\+\+\)/,
  "the leftward scan must start at the far end",
);
// Between two slots (covering neither by half) the last intent is HELD —
// clearing it would collapse every bystander for a frame and flicker.
assert.match(pointerMove, /publishDropMarker\(drag\.lastIntent\);/);
// ---- No "flung tab" on a fast flick ---------------------------------
// The dragged tab's transform is imperative, but React owns that
// element's style prop and drops the key on re-render. Without a
// re-assert the tab paints at its slot for a frame and then jumps to the
// pointer — a large discarded offset on a fast flick, which reads as the
// tab being flung. A layout effect restores it before paint.
assert.match(pointerMove, /drag\.lastTx = tx;/, "the clamped offset must be recorded");
assert.match(
  strip,
  /useLayoutEffect\(\(\) => \{[\s\S]*?pointerDragRef\.current[\s\S]*?drag\.element\.style\.transform = `translateX\(\$\{drag\.lastTx\}px\)`;/,
  "the drag offset must be re-asserted after every commit, before paint",
);
// Bystander shifts must never emit a transform for the dragged tab (it
// has no shift), or React would overwrite the live offset.
assert.match(geometry, /function shiftStyle/);
assert.match(geometry, /return shiftX \? \{ transform: `translateX\(\$\{shiftX\}px\)` \} : undefined;/);
// Let-through easing must stay linear-ish: an overshoot curve on the
// bystanders would look like the "force" being transmitted.
assert.match(css, /\.tab \{[^}]*transition: transform 160ms ease;/s);
assert.doesNotMatch(
  css,
  /transition:[^;]*cubic-bezier\([^)]*-[\d.]/,
  "no negative control points — a bouncy curve would overshoot the slot",
);
assert.match(
  pointerMove,
  /slotOverlapRatio\(drag\.targets\[selfIndex\], draggedRect\)/,
  "only a drag still covering its own slot clears the intent",
);
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
// (drag.lastTx is the clamped offset for the re-assert effect, not a
// direction: the reorder decision must not read travel direction.)
assert.doesNotMatch(
  pointerMove,
  /drag\.direction|drag\.lastX\b/,
  "reorder must not depend on travel direction",
);
assert.doesNotMatch(strip, /dwellRef|clearDwell/, "the tab dwell machinery is gone");
// Detach: GEOMETRIC trigger, not a distance dead-zone. Chrome tears off the
// instant the cursor leaves the tab strip's rectangle. NO 48px dead zone.
assert.doesNotMatch(strip, /DETACH_DISTANCE_PX/,
  "the distance dead-zone constant must be gone");
assert.doesNotMatch(pointerMove, /Math\.abs\(dy\) >/,
  "detach must not be gated on absolute pointer travel");
// The trigger reads the strip's vertical band (snapshotted from stripRef at
// drag start) and fires when the cursor is below the bottom / above the top.
assert.match(pointerMove, /drag\.stripTop = stripRect\.top;/,
  "the strip's vertical band must be snapshotted at drag start");
assert.match(pointerMove, /drag\.stripBottom = stripRect\.bottom;/);
assert.match(pointerMove, /e\.clientY > drag\.stripBottom \+ DETACH_HYSTERESIS_PX/,
  "detach must begin when the cursor leaves the strip's bottom edge");
assert.match(pointerMove, /e\.clientY < drag\.stripTop - DETACH_HYSTERESIS_PX/,
  "detach must also begin when the cursor leaves the strip's top edge");
// Asymmetric hysteresis: ENTER detach a full tab-height past the edge (above),
// but COME HOME the moment the cursor is back inside the strip rectangle — a
// symmetric inner band that wide is impossible on a ~40px strip. So cancel is
// keyed on the plain strip rect, not an inner-inset band.
assert.match(pointerMove, /e\.clientY <= drag\.stripBottom && e\.clientY >= drag\.stripTop/,
  "coming home (cancel) must trigger as soon as the cursor is back inside the strip rect");
// Drop-to-place: leaving the strip only shows detach-intent (translucent,
// slot closed). No window is created mid-drag — macOS starves JS timers
// during the button-held modal loop, so live cursor-follow is impossible.
assert.match(pointerMove, /data-detach-intent/);
assert.doesNotMatch(strip, /beginDetach|followCursorWithDetached|moveDetached/,
  "no mid-drag window creation or live-follow may survive (drop-to-place)");
assert.doesNotMatch(strip, /data-detached-away/,
  "the strip tab is never collapsed mid-drag — there is no live window to double it");
assert.doesNotMatch(strip, /detachRequested|detachedShown|followRequested|detachPromise/,
  "the live-follow drag-state fields must be gone");
// The detach-intent feedback (translucent grabbed tab) stays as pure drag
// feedback, but there is no data-detached-away opacity:0 collapse anymore.
assert.doesNotMatch(css, /data-detached-away/,
  "the opacity:0 collapse existed only to hide a live window — it must be gone");
assert.match(css, /\[data-detach-intent="true"\][\s\S]*?opacity: 0\.7/,
  "the detach-intent translucent feedback must remain");
// New-window cue: dragging out shows a clear "this becomes its own window"
// affordance — accent outline ON the dragged tab, plus a floating "New window"
// pill portaled OUTSIDE the strip (which clips vertical overflow, so an on-tab
// pill is invisible). No bottom banner, no disconnect badge.
assert.match(css, /\[data-detach-intent="true"\][\s\S]*?outline:[^;]*var\(--accent/,
  "the dragged tab must show an accent outline when releasing would split it out");
// The old ::after / data-detach-label pill was clipped by .tabsFlow overflow —
// it must be gone, replaced by the portaled floating cue.
assert.doesNotMatch(css, /data-detach-label/,
  "the clipped ::after pill (data-detach-label) must be removed");
assert.doesNotMatch(strip, /data-detach-label/,
  "the data-detach-label plumbing must be removed");
// The floating cue is set to the pointer position while detaching and cleared
// on every drag-exit path (clearDragState is the shared teardown).
assert.match(pointerMove, /setDetachCue\(\s*drag\.detaching \? \{ x: e\.clientX, y: e\.clientY \} : null/,
  "the floating cue must track the pointer while detaching and clear when not");
assert.match(strip, /function clearDragState\(\)[\s\S]*?setDetachCue\(null\)/,
  "the floating cue must be cleared on every drag-exit (clearDragState)");
// It must be portaled (fixed, outside the strip) and non-interactive so it
// never steals the pointer capture.
assert.match(strip, /createPortal\(\s*<div\s+className=\{styles\.detachCue\}/,
  "the floating cue must be portaled outside the strip");
assert.match(css, /\.detachCue \{[\s\S]*?position: fixed/,
  "the floating cue must be position:fixed (escapes the clipped strip)");
assert.match(css, /\.detachCue \{[\s\S]*?pointer-events: none/,
  "the floating cue must be pointer-events:none so it never breaks pointer capture");
// ---- Mutually-exclusive cross-window cues ---------------------------
// Over EMPTY desktop → "New window" pill (detachCue). Over ANOTHER window →
// that window's own "Add tab here" cue instead, so the source-side pill must
// hide whenever a hover target exists. The source polls window-at-cursor for
// the ENTIRE drag (not only detach) so the destination hover cue is adaptive —
// main clears the highlight on a null return, so it never latches.
assert.match(pointerMove, /if \(hasTransferToken && !detachHoverPollRef\.current\)/,
  "the source must poll window-at-cursor for the entire drag (a token exists), not only while detaching — gated on the raw token so a SOLO window still polls");
assert.match(pointerMove, /tabTransfer\.windowAtCursor/,
  "the source must poll window-at-cursor to detect a merge target");
assert.match(pointerMove, /const over = id !== null;[\s\S]*?setDetachOverTarget\(over\)/,
  "the source must record whether the drag cursor is over another window");
assert.match(strip, /\{detachCue && detachCueHost && !detachOverTarget/,
  "the source 'New window' pill must hide when a hover target exists (mutually exclusive)");
assert.match(strip, /function clearDragState\(\)[\s\S]*?setDetachOverTarget\(false\)/,
  "the over-target flag must clear on drag-exit");
// ---- Destination cross-window cue (TOP TAB STRIP only) --------------
// The destination window subscribes to onTransferHover; on enter it shows the
// cue CONFINED TO THE TOP STRIP — never a full-window/content-area overlay
// (that would read as split). The .strip gets an accent highlight and a small
// pill sits at the strip's bottom edge. Reduced-motion safe, pointer-events:none.
assert.match(strip, /onTransferHover/,
  "the destination must subscribe to the cross-window hover cue");
assert.match(strip, /setTransferHover\(entering\)/,
  "hover-enter/leave toggles the destination cue");
assert.match(strip, /data-transfer-hover=\{transferHover \|\| undefined\}/,
  "the strip itself carries the hover highlight — the cue lives in the top bar");
assert.match(strip, /\{transferHover \? \(\s*<div className=\{styles\.transferHoverPill\}/,
  "the destination pill renders inline in the strip, not portaled into the page body");
// The full-window inset overlay must be gone — no page-body glow / split look.
assert.doesNotMatch(strip, /transferHoverOverlay/,
  "the full-window overlay must be removed (it read as a split affordance)");
assert.doesNotMatch(css, /transferHoverOverlay/,
  "the full-window overlay CSS must be removed");
const transferHoverPillRule = css.slice(
  css.indexOf(".transferHoverPill {"),
  css.indexOf(" transferHoverIn"),
);
assert.doesNotMatch(transferHoverPillRule, /inset:\s*0/,
  "the destination cue must not span the content area");
assert.match(css, /\.strip\[data-transfer-hover\]/,
  "the strip band gets the hover highlight");
// Single accent token for the WHOLE cue — tint, bottom edge, and pill — no
// mismatched hardcoded rgba + different token.
const hoverBlock = css.slice(
  css.indexOf(".strip[data-transfer-hover]"),
  css.indexOf("@keyframes transferHoverIn"),
);
assert.doesNotMatch(hoverBlock, /rgba\(217, 119, 87/,
  "the cue must use the --accent-orange token, not a hardcoded orange rgba");
assert.doesNotMatch(hoverBlock, /var\(--accent-blue\)/,
  "the cue must not mix in --accent-blue — one token for tint + edge + pill");
assert.match(css, /\.strip\[data-transfer-hover\][\s\S]*?box-shadow: inset 0 -2px 0 var\(--accent-orange\)/,
  "the strip bottom edge uses --accent-orange");
assert.match(css, /\.transferHoverPill \{[\s\S]*?background: var\(--accent-orange\)/,
  "the pill background uses the same --accent-orange token");
// Top-left corner: the leftmost 88px (traffic-light inset) belong to the
// parent .desktop-tab-row, so it must tint too for one continuous band.
assert.match(css, /:global\(\.desktop-tab-row\):has\(\.strip\[data-transfer-hover\]\)/,
  "the desktop tab-row (left inset) must tint too so the top-left corner is covered");
const desktopChromeCss = readFileSync(
  new URL("../app/styles/base.css", import.meta.url),
  "utf8",
);
assert.match(
  appShell,
  /desktop-resize-edge desktop-resize-n[\s\S]*desktop-resize-w[\s\S]*desktop-resize-e[\s\S]*<CenterTabStrip/,
  "the desktop tab row must leave 5px no-drag edges for native resize",
);
assert.match(
  desktopChromeCss,
  /html\.is-desktop \.desktop-resize-n \{ top: 0; left: 0; right: 0; height: 5px; \}/,
  "the top resize inset must be a 5px no-drag strip",
);
assert.match(
  desktopChromeCss,
  /html\.is-desktop \.desktop-resize-edge \{[\s\S]*?-webkit-app-region: no-drag;/,
  "resize insets must be no-drag so they do not swallow the window edge",
);
assert.match(css, /\.transferHoverPill \{[\s\S]*?pointer-events: none/,
  "the destination pill must be pointer-events:none so it never blocks the drop");
assert.match(css, /@media \(prefers-reduced-motion: reduce\) \{\s*\.transferHoverPill \{\s*animation: none/,
  "the destination cue must be reduced-motion safe");
// ---- Pre-warm machinery is GONE -------------------------------------
// The tear-off window is created on demand when the cursor leaves the strip,
// never speculatively pre-warmed at drag start. None of the pre-warm names,
// timers, or debounce may survive.
assert.doesNotMatch(strip, /warmDetachWindow/, "warmDetachWindow must be gone");
assert.doesNotMatch(strip, /DETACH_WARM_DELAY_MS/, "DETACH_WARM_DELAY_MS must be gone");
assert.doesNotMatch(strip, /warmTimer/, "the warm-timer drag state must be gone");
assert.doesNotMatch(strip, /spareWindow|residentWindow|warmPool/,
  "no resident spare window may be introduced in place of the pre-warm");
const coordinator = readFileSync(
  new URL("../lib/tab-drag-coordinator.ts", import.meta.url),
  "utf8",
);
assert.doesNotMatch(coordinator, /DETACH_WARM_DELAY_MS/,
  "the pre-warm delay constant must be removed from the coordinator");
assert.match(coordinator, /export const DETACH_HYSTERESIS_PX/,
  "the coordinator must export the geometric hysteresis band");
const pointerUp = strip.slice(
  strip.indexOf("function onPointerDragUp"),
  strip.indexOf("function targetBeforeId"),
);
// Drop-to-place: the window is created at RELEASE via detach(token), not
// mid-drag. No detachPromise to await, no mid-drag window to dispose.
assert.doesNotMatch(pointerUp, /detachPromise|cancelDetach/,
  "release must not reference mid-drag window state (drop-to-place)");
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
assert.match(pointerUp, /const splitCapacityRejected = isSplitCapacityRejection\(/);
assert.match(pointerUp, /cancelDrag\(!splitCapacityRejected\)/);
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

// ---- Main process: drop-to-place -------------------------------------
// The torn-off window is created hidden at release, positioned at the drop
// point (clamped to the work area so it never spawns offscreen), then
// revealed at commit. No live-follow machinery survives.
const centerOnCursor = desktopMain.slice(
  desktopMain.indexOf("function centerHiddenWindowOnCursor"),
  desktopMain.indexOf("function showWindowSmoothly"),
);
assert.match(centerOnCursor, /clamp\s*\?[\s\S]*?Math\.max\(rawX/,
  "the drop-point placement must clamp to the work area so it never spawns offscreen");
// The doomed live-follow mechanism (macOS starves its timer) must be gone.
assert.doesNotMatch(desktopMain, /function startFollow|function stopFollow|function moveDetached|function cancelDetached/,
  "the live-follow / mid-drag window functions must be deleted (drop-to-place)");
assert.doesNotMatch(desktopMain, /followTimer|detachedShown|setInterval\([^)]*centerHiddenWindowOnCursor/,
  "no follow-timer state or interval may survive");
// detach() creates the window at the drop point and shares one in-flight boot
// across concurrent calls (release can race the window-at-cursor hit test).
assert.match(desktopMain, /if \(transaction\.detachPromise\) return transaction\.detachPromise;/,
  "concurrent detaches must share one in-flight window boot");
assert.match(desktopMain, /centerHiddenWindowOnCursor\(destination\.win\);/,
  "the torn-off window must be positioned at the drop point (clamped by default)");
// The window is revealed at COMMIT, once the destination renderer has staged
// the tab, so it never flashes empty.
assert.match(desktopMain, /const detached = liveContext\(transaction\.detachedWindowId\);[\s\S]*?if \(detached\) showWindowSmoothly\(detached\.win\)/,
  "the torn-off window must be revealed at commit (drop-to-place)");
// Detached windows keep the modest movable size cap (min so the drop point
// stays on-screen), and cancel/rollback closes them so nothing is orphaned.
const desktopWindowState = readFileSync(
  new URL("../../desktop/window-state.js", import.meta.url),
  "utf8",
);
assert.match(desktopWindowState, /DETACHED_MAX_WIDTH = 1100/,
  "detached windows must keep the 1100-wide size cap");
assert.match(desktopWindowState, /DETACHED_MAX_HEIGHT = 720/,
  "detached windows must keep the 720-tall size cap");
assert.match(desktopMain, /browserWindowOptionsForPlan\(state, \{ detached \}\)/,
  "detached windows must use the shared window-state size plan");
assert.match(desktopMain, /if \(!detached\) \{[\s\S]*?attachWindowStatePersistence/,
  "detached windows must stay ephemeral and not persist window-state");
assert.match(desktopMain, /function clearActive\([^)]*\) \{[\s\S]*?if \(closeHidden\) closeDetached\(transaction\.detachedWindowId\)/,
  "a rejected/rolled-back transfer must close its staged window — no orphans");

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
// ---- Activate on press (Chrome) --------------------------------------
// pointerdown selects the tab so it is live for the whole drag; the click
// that completes the same press must not re-activate it.
const pointerDown = strip.slice(
  strip.indexOf("function onTabPointerDown"),
  strip.indexOf("function onPointerDragMove"),
);
assert.match(pointerDown, /onTabClick\(pressed\)/, "pointerdown must activate the tab");
assert.match(
  pointerDown,
  /pressed\.id !== useCenterTabs\.getState\(\)\.activeId/,
  "only activate when it actually changes",
);
assert.match(pointerDown, /activatedOnPressRef\.current = pressed\.id;/);
assert.match(
  pointerDown,
  /const element = event\.currentTarget as HTMLElement;/,
  "whole-composite drag must transform the composite element, not its tab-flow parent",
);
// Right/middle button and an open context menu both return before this.
assert.ok(
  pointerDown.indexOf("event.button !== 0") < pointerDown.indexOf("onTabClick(pressed)"),
  "right-click must return before activating",
);
assert.ok(
  pointerDown.indexOf("tabMenuRef.current") < pointerDown.indexOf("onTabClick(pressed)"),
  "an open context menu must return before activating",
);
// A composite press carries no member-level activation; its click activates
// the canonical member only when the gesture remains a click.
assert.match(pointerDown, /subject\.kind !== "group"/);
// The follow-up click is consumed once, preserving click-to-reload for a
// genuine click on the already-active tab.
assert.match(strip, /function onTabClickFromPointer/);
assert.match(
  strip,
  /if \(activatedOnPressRef\.current === tab\.id\) \{[\s\S]*?return;/,
  "the click completing an activating press is a no-op",
);
assert.equal(
  strip.match(/onActivate=\{onTabClickFromPointer\}/g)?.length,
  2,
  "both plain tabs and the composite split tab use the press-aware click path",
);
assert.match(strip, /moveGroupMember\(/);
assert.match(strip, /moveGroup\(/);
assert.match(strip, /ungroupTab\(/);
assert.match(strip, /groupTab\(/);
const applyDrop = strip.slice(
  strip.indexOf("function applyDrop"),
  strip.indexOf("return { targetBeforeId, applyDrop }"),
);
assert.match(applyDrop, /subject\.kind === "group"[\s\S]*return mergeGroup\(/);
assert.doesNotMatch(applyDrop, /if \(subject\.kind === "group"\) return false;/);
assert.match(
  strip,
  /className=\{`\$\{styles\.compoundTab\}[\s\S]*?onPointerDown=\{\(event\) => onDragPointerDown\(\{[\s\S]*?kind: "group"/,
  "the composite tab itself must drag the whole split group",
);
assert.match(strip, /window\.addEventListener\("pointerup", cancelUnstarted, \{ once: true \}\)/);
assert.match(strip, /function isSplitCapacityRejection/);

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
  strip.indexOf("function canOpenSplitPicker"),
);
assert.match(moveMenuTab, /moveGroup\(/);
assert.doesNotMatch(moveMenuTab, /moveGroupMember\(|ungroupTab\(/);
assert.match(moveMenuTab, /moveTab\(/);
// ---- Split view picker (Chrome's "New Split View with Current Tab") ---
// The menu entry opens a picker instead of silently pairing with the
// active tab; the picker commits through the same groupTab store action.
assert.match(strip, /function canOpenSplitPicker/);
assert.match(
  strip,
  /function canOpenSplitPicker[\s\S]*?findCenterTabGroup\(state\.groups, tabId\)\) return false;/,
  "an existing composite tab cannot add another independently selectable member",
);
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
assert.match(
  picker,
  /latestState\.groupTab\(\s*tab\.id,\s*subjectId,\s*memberIndex,\s*subjectGroup\?\.id,?\s*\)/,
);
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
  "A split tab contains two views",
  "Tab move cancelled",
  "Tab moved to new window",
]) {
  assert.match(strip, new RegExp(announcement));
}
assert.match(strip, /function returnFocusToMenuInvoker/);
assert.match(strip, /if \(e\.key !== "Escape"\) return;[\s\S]*cancelCoordinator\(\)[\s\S]*setDropMarker\(null\)[\s\S]*setTabMenu\(null\)/);

const closeButtonStart = strip.indexOf(
  "<button\n        type=\"button\"",
  strip.indexOf("function TabItem"),
);
const closeButton = strip.slice(
  closeButtonStart,
  strip.indexOf("<X size={14} />", closeButtonStart),
);
assert.match(closeButton, /onPointerDown=\{\(event\) => event\.stopPropagation\(\)\}/);
assert.match(closeButton, /onMouseDown=\{\(event\) => event\.stopPropagation\(\)\}/);
assert.doesNotMatch(closeButton, /onDragPointerDown/);
// The dragged tab body follows the pointer: `transform` is written by JS
// every pointermove, so it must NEVER be transitioned (it would lag the
// cursor). Other visual properties may ease — the detach state fades and
// shrinks via `scale`, which composes with translateX instead of
// overwriting it. Raised above sliding bystanders, and FULLY OPAQUE over
// an opaque background (.tab is background:transparent by default —
// without a fill the dragged tab shows the tabs underneath through it).
const pointerDragRule = css.slice(
  css.indexOf('.tab[data-pointer-drag="true"]'),
  css.indexOf('.tab[data-pointer-drag="true"]::before'),
);
// Strip comments first: the explanatory comment inside the rule mentions
// `transform`, which would otherwise trip the assertion below.
const pointerDragDecls = pointerDragRule.replace(/\/\*[\s\S]*?\*\//g, "");
const pointerDragTransition = /transition:([^;]*);/.exec(pointerDragDecls)?.[1] ?? "";
assert.doesNotMatch(
  pointerDragTransition,
  /\btransform\b|\ball\b/,
  "the follow-the-pointer tab must never transition transform — JS owns it per frame",
);
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
assert.match(geometry, /function computeLiveShifts\(/);
assert.match(
  geometry,
  /marker\.mode === "merge"[^)]*\) return shifts;/,
  "merge intents must not shift bystanders — highlight and slide are exclusive",
);
assert.match(
  geometry,
  /translateX\(\$\{shiftX\}px\)/,
  "shifted entries must move via transform, never layout",
);
assert.match(
  geometry,
  /Static slot geometry captured at drag start/,
  "drop intent math must use untransformed slot geometry",
);
assert.doesNotMatch(
  strip,
  /dropIntent/,
  "tabs no longer surface any merge intent — dragging only reorders",
);

// 72px = ＋号 36 (8px gap + 28px) + 主菜单钮 36, so the menu button lands
// on the 49px right rail and the ＋ stays in natural flow before it.
assert.match(css, /max-width: calc\(100% - 72px\);/);
assert.equal(finalDeclaration(":global(html.is-desktop) .strip", "height"), "40px");
assert.equal(finalDeclaration(":global(html.is-desktop) .strip", "align-items"), "center");
assert.equal(finalDeclaration(":global(html.is-desktop) .tabsFlow", "align-items"), "center");
assert.match(css, /:global\(html\.is-desktop\) \.strip \{[^}]*padding-right: 10px;/s);
assert.match(
  css,
  /:global\(html\.is-desktop\) \.tabsFlow \{[^}]*overflow-x: auto;[^}]*overflow-y: hidden;[^}]*scrollbar-width: none;/s,
);
assert.match(
  css,
  /\.strip \{[^}]*--tab-active-bg: var\(--bg-primary\);/s,
  "browser tabs must retain the content-surface active background",
);
assert.match(
  css,
  /:global\(html\.is-desktop\) \.strip \{[^}]*--tab-active-bg: var\(--bg-primary\);/s,
  "desktop selected tabs must use the brighter content surface",
);
assert.match(css, /\.tabActive,\s*\.tab\.tabActive:hover \{[^}]*background: var\(--tab-active-bg\);/s);
assert.match(css, /\.compoundTabActive \{[^}]*background: var\(--tab-active-bg\);/s);
assert.match(css, /\.tab\.tabActive::after \{[^}]*var\(--tab-active-bg\) 8px/s);
assert.match(css, /\.tab\.tabActive::before \{[^}]*var\(--tab-active-bg\) 8px/s);
assert.match(css, /\.compoundTabActive::before \{[^}]*var\(--tab-active-bg\) 8px/s);
assert.match(css, /\.compoundTabActive::after \{[^}]*var\(--tab-active-bg\) 8px/s);
assert.match(
  css,
  /:global\(html\.is-desktop\) \.tabsFlow::\-webkit-scrollbar \{[^}]*display: none;/s,
);
assert.match(css, /:global\(html\.is-desktop\) \.tabsFlow > \.tab \{[^}]*width: 240px;[^}]*flex: 0 1 240px;[^}]*max-width: 240px;/s);
assert.match(
  css,
  /:global\(html\.is-desktop\) \.tab \{[^}]*padding-right: 5px;[^}]*border-radius: 8px;/s,
  "desktop tab close controls retain their compact right inset",
);
assert.equal(finalDeclaration(":global(html.is-desktop) .tab", "height"), "28px");
assert.equal(finalDeclaration(":global(html.is-desktop) .tab.tabActive", "height"), "28px");
assert.equal(finalDeclaration(":global(html.is-desktop) .tab.tabActive", "border-radius"), "8px");
assert.equal(finalDeclaration(":global(html.is-desktop) .tab.tabActive", "box-shadow"), "none");
assert.equal(finalDeclaration(":global(html.is-desktop) .compoundTab", "height"), "28px");
assert.equal(finalDeclaration(":global(html.is-desktop) .compoundTabActive", "height"), "28px");
assert.equal(finalDeclaration(":global(html.is-desktop) .compoundTabActive", "border-radius"), "8px");
assert.equal(finalDeclaration(":global(html.is-desktop) .compoundTabActive", "box-shadow"), "none");
assert.equal(finalDeclaration(".tabClose", "width"), "20px");
assert.equal(finalDeclaration(".tabClose", "height"), "20px");
assert.match(strip, /<X size=\{14\} \/>/);
assert.equal(finalDeclaration(".tabEnter", "animation"), "tabEnter 180ms ease-out");
assert.doesNotMatch(
  css,
  /desktopTabEnter|:global\(html\.is-desktop\) \.tabEnter/,
  "desktop new tabs must retain the visible width-expansion animation",
);
assert.match(css, /\.tabExit \{[^}]*animation: tabExit 160ms ease-in forwards;/s);
assert.doesNotMatch(
  css,
  /:global\(html\.is-desktop\) \.tabExit/,
  "desktop closes must retain the full width-collapse duration",
);
assert.match(css, /@keyframes compoundMemberCollapse \{[^}]*width: var\(--compound-collapse-width\);[^}]*flex-basis: var\(--compound-collapse-width\);[^}]*max-width: var\(--compound-collapse-width\);/s);
assert.match(css, /\.compoundMemberClosing \{[^}]*animation: compoundMemberCollapse 160ms ease-in forwards;/s);
assert.match(css, /\.compoundMemberExit \{[^}]*animation: compoundMemberExit 160ms ease-in forwards;/s);
assert.match(strip, /centerTabStripEntries/);
assert.match(strip, /function CompoundTabItem/);
const compoundItem = strip.slice(
  strip.indexOf("function CompoundTabItem"),
  strip.indexOf("/** One strip tab."),
);
assert.match(compoundItem, /styles\.compoundTab/);
assert.equal(compoundItem.match(/role="tab"/g)?.length, 1);
assert.doesNotMatch(compoundItem, /<TabItem|group\.memberIds\.map/);
assert.match(compoundItem, /styles\.compoundMembers/);
assert.match(compoundItem, /memberTabs\.map\(\(tab\) =>/);
assert.match(compoundItem, /styles\.compoundMember/);
assert.match(compoundItem, /styles\.compoundMemberClose/);
assert.match(compoundItem, /onClose\(event, \[tab\]\)/);
assert.doesNotMatch(compoundItem, /<Columns2\b|\bColumns2\b/);
assert.match(compoundItem, /closingTabs\.length === memberTabs\.length/);
assert.match(compoundItem, /const memberClosing = closingTabs\.length > 0 && !closing;/);
assert.match(compoundItem, /memberClosing \? styles\.compoundMemberClosing : ""/);
assert.match(compoundItem, /const \[hovered, setHovered\] = useState\(false\);/);
assert.match(compoundItem, /onMouseEnter=\{\(\) => setHovered\(true\)\}/);
assert.match(compoundItem, /onMouseLeave=\{\(\) => setHovered\(false\)\}/);
assert.match(compoundItem, /<CompoundMemberIcon tab=\{tab\} animate=\{hovered\} \/>/);
assert.match(
  strip,
  /function CompoundMemberIcon\(\{ tab, animate \}[\s\S]*?MessageCircleIcon ref=\{iconRef\}/,
  "hovering the single composite entry must still animate its session icons",
);
assert.doesNotMatch(
  compoundItem,
  /enteringIds|styles\.tabEnter/,
  "a composite entry must not retain the ordinary tab enter class",
);
assert.match(
  compoundItem,
  /const tabStop = group\.memberIds\.includes\(focusedTabId \?\? ""\);/,
  "a composite entry participates in the same single roving tab stop as ordinary tabs",
);
assert.match(strip, /onAnimationEnd/);
assert.match(compoundItem, /closingTabs\.forEach\(onExited\)/);
assert.match(strip, /onExited=\{finishClose\}/);
assert.match(strip, /onClose=\{onTabClose\}/);
assert.match(strip, /onClose=\{onTabsClose\}/);
assert.doesNotMatch(strip, /splitPinned|data-split-pinned/);
assert.doesNotMatch(
  css,
  /data-split-pinned|inset 0 2px 0 var\(--accent-blue\)/,
);
assert.match(
  css,
  /\.compoundTab \{[^}]*width:\s*280px;[^}]*flex:\s*0 1 280px;[^}]*max-width:\s*280px;/s,
  "the composite tab uses one bounded top-level tab width",
);
assert.match(
  css,
  /\.compoundTab \{[^}]*min-width:\s*136px;/s,
  "the composite tab must not shrink below two non-overlapping icon and close targets",
);
assert.match(
  css,
  /\.compoundMembers \{[^}]*display:\s*grid;[^}]*grid-template-columns:\s*repeat\(2, minmax\(0, 1fr\)\);/s,
  "split members must always receive equal top-strip width",
);
assert.match(
  css,
  /\.compoundTarget \{[^}]*z-index:\s*1;/s,
  "the single role=tab target must receive pointer focus across both member bodies",
);
assert.match(
  css,
  /\.compoundMembers \{[^}]*pointer-events:\s*none;/s,
  "member presentation must not intercept the composite tab target",
);
assert.match(
  css,
  /\.compoundMemberClose \{[^}]*position:\s*relative;[^}]*z-index:\s*2;[^}]*pointer-events:\s*auto;/s,
  "each member close button must remain independently clickable above the composite target",
);
assert.match(compoundItem, /aria-disabled=\{closingIds\.has\(tab\.id\) \|\| undefined\}/);
assert.doesNotMatch(compoundItem, /\bdisabled=\{closingIds\.has\(tab\.id\)\}/);
assert.match(
  compoundItem,
  /onClick=\{\(event\) => \{\s*event\.stopPropagation\(\);\s*if \(!closingIds\.has\(tab\.id\)\) onClose\(event, \[tab\]\);\s*\}\}/,
  "a closing member must keep intercepting pointer clicks without starting another close or group drag",
);
assert.doesNotMatch(css, /\.compoundMemberClose:disabled/);
assert.match(css, /\.compoundMember \{[^}]*min-width:\s*0;/s);
assert.match(css, /\.compoundMemberName \{[^}]*overflow:\s*hidden;[^}]*text-overflow:\s*ellipsis;/s);
assert.doesNotMatch(css, /groupDragHandle|data-closing-count/);
assert.match(
  compoundItem,
  /onPointerDown=\{\(event\) => onDragPointerDown\(\{[\s\S]*?kind: "group"[\s\S]*?tabIds: \[\.\.\.group\.memberIds\]/,
  "the outer composite remains the only drag subject",
);
assert.match(
  strip,
  /window\.addEventListener\("op-desktop-close-tab", onDesktopClose\)/,
  "the strip lifecycle must route Cmd+W through the whole-entry close transaction",
);
assert.doesNotMatch(
  desktopBridge,
  /op-desktop-close-tab[\s\S]{0,300}closeTab\(/,
  "the desktop bridge must not bypass the strip's dirty-check and composite close path",
);
assert.match(
  strip,
  /tabRef\.current\?\.closest<HTMLElement>\('\[role="tablist"\]'\)/,
  "ordinary tabs must observe the actual tab flow",
);
assert.doesNotMatch(
  strip.slice(strip.indexOf("function TabItem")),
  /tabRef\.current\?\.parentElement/,
);
assert.match(ntp, /const draftId = useCenterTabs\.getState\(\)\.claimDraftSessionTab\(\);[\s\S]*newSession\(draftId\);/);
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
// The reserved right column belongs to the main menu button now, so the
// ＋ is a plain flex item after the tab flow: no rail pinning, no
// measuring, no plusRef.
assert.doesNotMatch(
  strip,
  /data-plus-rail-aligned|plusRef/,
  "the + must no longer be pinned to the right rail",
);
assert.match(strip, /<MainMenu \/>/, "the strip must host the main menu button");
assert.ok(
  strip.indexOf("styles.plusBtn") < strip.indexOf("<MainMenu />"),
  "the main menu button must follow the + button",
);
assert.equal(finalDeclaration(".plusBtn", "width"), "28px");
assert.equal(finalDeclaration(".plusBtn", "height"), "28px");
assert.equal(finalDeclaration(".menuBtn", "margin-left"), "auto");
assert.equal(finalDeclaration(".menuBtn", "width"), "28px");
assert.equal(finalDeclaration(".menuBtn", "height"), "28px");
assert.match(
  css,
  /:global\(html\.is-desktop\) \.menuBtn \{\s*-webkit-app-region: no-drag;|:global\(html\.is-desktop\) \.plusBtn,\s*:global\(html\.is-desktop\) \.menuBtn \{[^}]*-webkit-app-region: no-drag;/s,
  "the desktop menu button must stay clickable inside the drag region",
);
assert.match(strip, /stripRef/);
assert.match(strip, /tabsFlowRef/);
assert.match(
  css,
  /\.plusBtn::before \{[^}]*width: 2px;[^}]*border-radius: 1px;[^}]*background: var\(--plus-separator-background, var\(--border\)\);/s,
  "short desktop tab lists must retain the normal 2px rounded divider",
);
assert.match(
  css,
  /\.tabsFlow:has\(> \.tabActive:last-child\) \+ \.plusBtn::before,\s*\.tabsFlow:has\(> \.tab:last-child:hover\) \+ \.plusBtn::before,\s*\.plusBtn:hover::before \{\s*background: transparent;/,
  "active and hovered tabs must still hide the normal + divider",
);
assert.doesNotMatch(
  css,
  /data-plus-rail-aligned/,
  "the + rail-pinning rules must stay deleted",
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
  "registerVisibleWebTabBounds(bridge, tabId, bounds)",
);
assert.ok(ensureIndex >= 0 && ensureIndex < registerBoundsIndex);
assert.equal(navigateIndex, -1);
assert.doesNotMatch(webTabPane, /bridge\.webTab\.(?:show|hide|setBounds)\(/);
assert.match(fileTree, /openFileTab\(projectId, path\);[\s\S]*navigate\("\/chat"\)/);

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

// ---- center perspectives: transcript ⇄ session context DAG ----------
// The graph moved out of the right sidebar into the chat pane, switched by
// the Obsidian-style controls at the pane's top-right. State is per tab.
const centerTabsStore = readFileSync(
  new URL("../lib/state/center-tabs-store.ts", import.meta.url),
  "utf8",
);
assert.match(
  centerTabsStore,
  /setTabDagView: \(id, dagView\) =>/,
  "the per-tab perspective setter must live in the center-tabs store",
);
// Both surfaces stay mounted: the DAG renderer paints into its host on every
// capture, so unmounting it would blank the graph until the next one.
assert.match(
  appShell,
  /<PageShell page="chat" \/>\s*<DagView visible=\{activeTabDagView\} \/>\s*<ViewControls \/>/,
  "the chat pane must host the transcript, the DAG and the view controls together",
);
assert.match(
  appShell,
  /data-center-view=\{activeTabDagView \? "dag" : "session"\}/,
  "the perspective must be a display swap driven off the active tab",
);
const chatCss = readChatCss(new URL("../", import.meta.url));
// The graph replaces the TRANSCRIPT, not the whole `#chatView`. The
// composer is a singleton portalled into `#composer-mount` inside
// `#chatView` and anchored `bottom:0` against it, so hiding that
// ancestor would take the composer with it and the graph perspective
// would lose the ability to send a message.
assert.match(
  chatCss,
  /\.center-pane-chat\[data-center-view="dag"\] #chatArea \{\s*display: none;/,
  "the DAG perspective must hide the transcript scroll area",
);
assert.doesNotMatch(
  chatCss,
  /\[data-center-view="dag"\] #chatView \{\s*display: none;/,
  "hiding #chatView would take the composer down with the transcript",
);
// The graph COVERS the pane rather than taking #chatView's slot in the
// column, so the composer's `bottom: 0` still lands on the pane's bottom
// edge and the input box does not move when the perspective flips.
assert.match(
  chatCss,
  /\.center-pane-chat\[data-center-view="dag"\] \.dag-view \{[^}]*position: absolute;/,
  "the graph must overlay the pane so the composer keeps its anchor",
);
const viewControls = readFileSync(
  new URL("../components/chat/view-controls.tsx", import.meta.url),
  "utf8",
);
// Tooltip names the CURRENT perspective and what the click does (Obsidian).
assert.match(viewControls, /Current view: context graph\. Click to switch/);
assert.match(viewControls, /Current view: conversation\. Click to switch/);
// The overflow menu is a second entry point for actions the Recents row
// already ships — same WS verbs, nothing invented here.
for (const verb of [
  "rename_session",
  "update_session_flags",
  "delete_session",
]) {
  assert.match(
    viewControls,
    new RegExp(`action: "${verb}"`),
    `the session menu must reuse the ${verb} verb`,
  );
}

// ---- Chrome's close-with-the-mouse width freeze ---------------------
// Clicking × must pin the survivors to their current pixel widths so the
// next tab's × lands under the cursor already there; the pin is released
// when the pointer leaves the strip, and immediately by anything that has
// to relayout anyway (new tab, drag, window resize).
assert.match(geometry, /export function freezeStripWidths/);
assert.match(geometry, /export function releaseStripWidths/);
// The lock must beat BOTH the browser `flex: 0 1 200px` rule and the
// desktop 240px override, so all three sizing properties are pinned.
const freezeBody = geometry.slice(
  geometry.indexOf("export function freezeStripWidths"),
  geometry.indexOf("export function releaseStripWidths"),
);
for (const property of ["width", "flexBasis", "maxWidth"]) {
  assert.match(
    freezeBody,
    new RegExp(`child\\.style\\.${property} = px;`),
    `the freeze must pin ${property} — a partial lock loses to the stylesheet`,
  );
}
// flex-shrink is the one that actually makes it hold: a crowded strip
// shrinks every tab BELOW its basis, so closing one relieves the overflow
// and the survivors shrink less — they still grow, pinned basis or not.
// Verified against the DOM: without this the second tab went 161→172px.
assert.match(
  freezeBody,
  /child\.style\.flexShrink = "0";/,
  "the freeze must pin flex-shrink, or survivors of a crowded strip still grow",
);
assert.match(
  geometry.slice(geometry.indexOf("export function releaseStripWidths")),
  /child\.style\.flexShrink = "";/,
  "the release must restore flex-shrink with the other three properties",
);
// A tab mid-exit animates its own width to zero; pinning it would freeze
// the close animation, so the freeze skips it (and the strip marks it).
assert.match(
  geometry,
  /if \(child\.dataset\.tabClosing === "true"\) continue;/,
  "a closing tab must keep its shrinking width, not be pinned",
);
assert.match(strip, /data-tab-closing=\{closing \|\| undefined\}/);
// Releasing must ease, not snap: the marker survives the width clear so
// the transition rule still matches while the survivors grow back.
assert.match(
  geometry,
  /child\.dataset\.widthFrozen = "released";/,
  "the marker must outlive the width clear, or the release snaps",
);
assert.match(
  css,
  /\.tab\[data-width-frozen="released"\],\s*\.compoundTab\[data-width-frozen="released"\] \{[^}]*transition:[^}]*width 160ms ease,[^}]*flex-basis 160ms ease,[^}]*max-width 160ms ease,/s,
  "released widths must ease back via a transition scoped to RELEASED tabs only",
);
// The pin state ("true") must NOT carry the width transition: a transition
// active at pin time animates flex-basis/max-width from the stylesheet
// values (240px) to the pinned pixels, ballooning the whole row for a
// frame — the first-close flicker (observed 141→240→141).
assert.doesNotMatch(
  css,
  /\.tab\[data-width-frozen\](?!=)[^,{]*[,{]/,
  "the width transition must not match the pinned state",
);
assert.match(
  css,
  /\.tab \{[^}]*transition: transform 160ms ease;/s,
  "the base .tab transition must stay transform-only — the drag let-through depends on it",
);
// 1. Mouse close freezes; keyboard and programmatic closes do not.
assert.match(strip, /function isMouseDrivenClose/);
assert.match(
  strip,
  /native instanceof MouseEvent\s*&& native\.detail > 0/,
  "only a physical click freezes — detail===0 is a keyboard activation of the ×",
);
assert.match(
  strip,
  /if \(isMouseDrivenClose\(e\)\) freezeWidthsForMouseClose\(e\.target\);\s*else releaseFrozenWidths\(\);/,
  "every non-mouse close path must reflow immediately",
);
// The × click's row entry is marked closing BEFORE the pin walks the row —
// React stamps data-tab-closing a render too late, and a pinned closing tab
// would fight its own exit shrink with the inline width.
assert.match(
  strip,
  /entry\.dataset\.tabClosing = "true";[\s\S]*?freezeStripWidths\(flow\);/,
  "the closing row entry must be marked before the pin",
);
// A container resize without a window resize (sidebar toggle, split drag)
// must also release: the survivors are flex-shrink:0 and would overflow.
assert.match(
  strip,
  /new ResizeObserver\([\s\S]*?releaseFrozenWidths\(\)/,
  "a strip-box resize must release the freeze",
);
const onTabClose = strip.slice(
  strip.indexOf("function onTabsClose"),
  strip.indexOf("function finishClose"),
);
assert.ok(
  onTabClose.indexOf("window.confirm") < onTabClose.indexOf("isMouseDrivenClose"),
  "a close cancelled at the discard prompt must not freeze the strip",
);
// 2. Leaving the strip releases, after a grace period; re-entry cancels it.
assert.match(strip, /const UNFREEZE_GRACE_MS = \d+;/);
assert.match(
  strip,
  /onMouseLeave=\{\(\) => \{[\s\S]*?setTimeout\(\s*releaseFrozenWidths,\s*UNFREEZE_GRACE_MS,\s*\)/,
  "leaving the whole strip must release the frozen widths",
);
assert.match(
  strip,
  /onMouseEnter=\{\(\) => \{[\s\S]*?clearTimeout\(unfreezeTimerRef\.current\)/,
  "re-entering before the grace period expires must keep the row pinned",
);
// 3+4. Drag, new tab and window resize each release immediately.
assert.match(
  strip,
  /window\.addEventListener\("resize", releaseFrozenWidths\)/,
  "a window resize must release the freeze — the row relayouts anyway",
);
assert.match(
  strip,
  /window\.removeEventListener\("resize", releaseFrozenWidths\)/,
);
assert.match(
  strip,
  /function onOpenNewTab\(\) \{[\s\S]*?releaseFrozenWidths\(\);[\s\S]*?openNewTabPage\(\)/,
  "opening a new tab must release the freeze before the enter animation",
);
assert.ok(
  strip.indexOf("releaseFrozenWidths();", strip.indexOf("function onTabPointerDown"))
    < strip.indexOf("onPrepareDrag(subject)"),
  "a press must release the freeze before the drag engine snapshots slot geometry",
);

console.log("center-tabs checks passed");
