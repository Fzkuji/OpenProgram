import { useCenterTabs } from "../center-tabs-store";
import { rowLayout, type CanvasDockSide, type Rect } from "./canvas-layout";
import { closeOverview, holdOverviewLanding, landOverview, openOverview, OVERVIEW_SIDE, overviewDropAt, overviewHeld, overviewOpen, useCanvasOverview } from "./canvas-overview";
import { captureCanvasTransition } from "./canvas-transition";
import { captureVisiblePanes } from "./window-snapshots";
import { findCenterTabGroup } from "../center-tab-groups";
import { rememberCanvas } from "./canvas-history";
import { dropWebTabResource, resourceDropTarget } from "../transfer/tab-resource-drop";
import { desktopBridge, buildTransferPayload } from "../../desktop/desktop-bridge";
import { translateText } from "../../i18n";
import { setCanvasDragFlag } from "./canvas-motion";
import { DRAG_SURFACE_STYLE } from "../../prefs/appearance/visual-effects-config";
/** A drop in the window overview. "dock" merges the tab into the window
 *  that holds targetId, beside the whole window; "fill" opens it on its
 *  own, in the strip before the tab `before` (null: last; absent: where
 *  it is). */
export type CanvasDrop = { targetId: string; paneId: string; side: CanvasDockSide; rect: Rect; action: "dock" | "fill"; label: string; before?: string | null };
let preview: HTMLDivElement | null = null;
let dragging = false;

/** The drag that owns the canvas right now. Every drag (tab, pane, divider)
 *  registers how to cancel itself and, when it can, how to finish. */
export type CanvasDragOwner = { cancel: () => void; release?: (event: PointerEvent) => void };
let owner: CanvasDragOwner | null = null;
let guarded = false;
/** A release over the window-drag strip, a native web view or another app
 *  never reaches the page, which used to leave a drag half-finished: the
 *  grip hidden, the label floating, iframes deaf. These guards end it. */
function installGuards() {
  if (guarded || typeof window === "undefined") return;
  guarded = true;
  // The first move with no button down is the release we missed.
  window.addEventListener("pointermove", event => {
    if (!owner || event.buttons !== 0 || event.pointerType === "touch") return;
    const current = owner;
    if (current.release) current.release(event); else current.cancel();
  }, true);
  // A new press while a drag is still open means its release was lost.
  window.addEventListener("pointerdown", () => owner?.cancel(), true);
  document.addEventListener("visibilitychange", () => { if (document.hidden) owner?.cancel(); });
}
export function setCanvasDragging(value: boolean, by?: CanvasDragOwner) {
  if (value) {
    installGuards();
    if (owner && by && owner !== by) { const stale = owner; owner = null; stale.cancel(); }
    if (by) owner = by;
  } else {
    owner = null;
    if (useCanvasOverview.getState().mode === "drag" && !overviewHeld()) closeOverview();
  }
  if (dragging === value) { if (!value) { preview?.remove(); preview = null; } return; }
  dragging = value;
  document.documentElement.toggleAttribute("data-canvas-dragging", value);
  setCanvasDragFlag(value);
  if (!value) { preview?.remove(); preview = null; }
}
/** Apply a canvas drop. Returns false when it changed nothing. */
export function commitCanvasDrop(drop: CanvasDrop, sourceIds: string[]): boolean {
  const s = useCenterTabs.getState(), sourceId = sourceIds[0];
  if (!sourceId) return false;
  const group = findCenterTabGroup(s.groups, drop.targetId);
  const layout = group?.canvas ?? rowLayout([drop.targetId]);
  if (drop.action === "fill") {
    // targetId is the dragged tab: `group` is the window it comes from.
    const whole = group?.memberIds.every(id => sourceIds.includes(id));
    if (group && !whole) rememberCanvas([drop.targetId, ...sourceIds, group.id, ...group.memberIds], layout);
    if (sourceIds.length === 1 && group && !whole) s.ungroupTab(sourceId);
    if (drop.before !== undefined) {
      if (group && whole) useCenterTabs.getState().moveGroup(group.id, drop.before);
      else useCenterTabs.getState().moveTab(sourceId, drop.before);
    }
    // A tab taken out of a split is a new window: show it. A window only
    // moved along the strip stays as it was.
    if (sourceIds.length === 1 && group && !whole) useCenterTabs.getState().setActive(sourceId);
    // The overview browses on (holdOverviewLanding); the tab's card is back.
    if (useCanvasOverview.getState().mode === "browse") useCanvasOverview.setState({ sourceIds: [] });
    return true;
  }
  // Without the overview the tab lands on the live canvas: it slides in
  // from its side as the window gives up the room.
  const live = !overviewOpen();
  const dock = () => {
    if (live) captureCanvasTransition({}, { [sourceId]: drop.side as "left" | "right" | "top" | "bottom" });
    rememberCanvas([drop.targetId, ...sourceIds, ...(group ? [group.id, ...group.memberIds] : [])], layout);
    useCenterTabs.getState().dockCanvas(sourceIds, drop.targetId, drop.paneId, drop.side);
  };
  // A drop landed through the overview commits when its zoom is done.
  if (!landOverview(dock)) dock();
  return true;
}
const LIVE_SIDE_LABEL: Record<CanvasDockSide & ("left" | "right" | "top" | "bottom"), [string, string]> = {
  left: ["Release to split, on the left", "松开以分屏，放在左边"],
  right: ["Release to split, on the right", "松开以分屏，放在右边"],
  top: ["Release to split, on top", "松开以分屏，放在上面"],
  bottom: ["Release to split, below", "松开以分屏，放在下面"],
};
/** A tab dragged out of the strip over the window on screen, with no
 *  overview open: it docks beside that window, on the side under the
 *  pointer (outer quarters left and right, otherwise the top or bottom
 *  half), and the cue shows the half it will take. Null off the canvas,
 *  or when the dragged tab is already part of the window shown. */
export function liveCanvasDropAt(x: number, y: number, sourceIds: string[]): CanvasDrop | null {
  if (overviewOpen() || !sourceIds.length) return null;
  const root = document.querySelector<HTMLElement>("[data-canvas-root]");
  const s = useCenterTabs.getState();
  const targetId = s.activeId;
  if (!root || !targetId) return null;
  const members = findCenterTabGroup(s.groups, targetId)?.memberIds ?? [targetId];
  if (sourceIds.some(id => members.includes(id))) return null;
  const b = root.getBoundingClientRect();
  if (b.width <= 0 || b.height <= 0 || x < b.left || x > b.right || y < b.top || y > b.bottom) return null;
  const px = (x - b.left) / b.width, py = (y - b.top) / b.height;
  const side = px < OVERVIEW_SIDE ? "left" : px > 1 - OVERVIEW_SIDE ? "right" : py < 0.5 ? "top" : "bottom";
  const rect: Rect = side === "left" ? { left: b.left, top: b.top, width: b.width / 2, height: b.height }
    : side === "right" ? { left: b.left + b.width / 2, top: b.top, width: b.width / 2, height: b.height }
    : side === "top" ? { left: b.left, top: b.top, width: b.width, height: b.height / 2 }
    : { left: b.left, top: b.top + b.height / 2, width: b.width, height: b.height / 2 };
  const label = translateText(...LIVE_SIDE_LABEL[side]);
  return { targetId, paneId: "root", side, rect, action: "dock", label };
}
/** The floating cue for a live canvas drop: the half the tab will take. */
export function showLiveCanvasDrop(drop: CanvasDrop) {
  showDropPreview(drop.rect, "canvas");
  if (!preview) return;
  preview.dataset.dropAction = "dock";
  const label = preview.querySelector(".canvas-drop-label") ?? document.createElement("span");
  label.className = "canvas-drop-label";
  label.textContent = drop.label;
  if (!label.isConnected) preview.replaceChildren(label);
}
export function tabStripDropAt(x: number, y: number): Rect | null {
  const rect = document.querySelector('[role="tablist"]')?.getBoundingClientRect();
  return rect && x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom
    ? { left: rect.left, top: rect.top, width: rect.width, height: rect.height } : null;
}
/** The overview draws its own drop cue; this clears the floating one. */
export function clearDropPreview() { showDropPreview(null); }
export function showResourceDrop(target: HTMLElement) {
  // An inactive Resources view accepts drops only on its navigation row.
  // An expanded view highlights its panel separately, below that row.
  const sidebar = target.closest(".right-sidebar");
  const row = sidebar?.querySelector<HTMLElement>('.right-nav-item[data-view="resources"]');
  const highlight = row && row.dataset.resourceDropSession === target.dataset.resourceDropSession ? row : target;
  let rect: Rect = highlight.getBoundingClientRect();
  const panel = sidebar?.querySelector<HTMLElement>('#sessionResourcesPanel[data-resource-drop-session]');
  if (row && row.getAttribute("aria-expanded") === "true" && panel
      && panel.dataset.resourceDropSession === target.dataset.resourceDropSession) {
    const bounds = panel.getBoundingClientRect();
    const top = Math.max(bounds.top, row.getBoundingClientRect().bottom + 4);
    if (bounds.width > 0 && bounds.bottom > top) {
      rect = { left: bounds.left, top, width: bounds.width, height: bounds.bottom - top };
    }
  }
  if (rect.width <= 0 || rect.height <= 0) { showDropPreview(null); return; }
  showDropPreview({ left: rect.left, top: rect.top, width: rect.width, height: rect.height }, "resources");
  if (preview) {
    preview.dataset.compact = String(rect.height < 80);
    const label = document.createElement("span");
    label.className = "canvas-drop-label";
    label.textContent = translateText("Release to add to Resources", "松开以添加到资源");
    preview.replaceChildren(label);
  }
}
function showDropPreview(rect: Rect | null, target = "canvas") {
  if (!rect) { preview?.remove(); preview = null; return; }
  // Styled by .canvas-drop-preview (app/styles/base/canvas-drag.css): soft system
  // blue with a slight spring as it moves between drop zones.
  if (!preview) { preview = document.createElement("div"); preview.className = "canvas-drop-preview"; document.body.append(preview); }
  if (preview.dataset.dropTarget !== target) { preview.replaceChildren(); delete preview.dataset.compact; delete preview.dataset.dropAction; }
  preview.dataset.dropTarget = target;
  const inset = target === "resources" ? 0 : 4;
  Object.assign(preview.style, { left: `${rect.left + inset}px`, top: `${rect.top + inset}px`, width: `${Math.max(0, rect.width - inset * 2)}px`, height: `${Math.max(0, rect.height - inset * 2)}px` });
}
/** Drag a pane by its grip. The pane, not the grip's inline style, carries
 *  the dragging state, and every way a drag can end (release, Escape,
 *  pointercancel, blur, a missed release, an error while resolving the
 *  drop) runs the same cleanup exactly once. */
export function startPaneDrag(event: React.PointerEvent<HTMLElement>, tabId: string, label: HTMLElement | null) {
  // The grip's window buttons drag like the rest of it; other buttons do not.
  if (event.button !== 0 || !label || (event.target as HTMLElement).closest("button:not([data-pane-light])")) return;
  // Keep the press from starting a text selection in the pane below.
  event.preventDefault();
  const grip = event.currentTarget;
  const pane = grip.closest<HTMLElement>("[data-canvas-pane]") ?? grip;
  // A drag that ended without cleanup must not leave another grip hidden.
  document.querySelectorAll("[data-pane-dragging]").forEach(node => node.removeAttribute("data-pane-dragging"));
  let dragLabel: HTMLElement | null = null;
  let resourceTarget: HTMLElement | null = null;
  function markResourceTarget(target: HTMLElement | null) {
    if (resourceTarget === target) return;
    resourceTarget?.removeAttribute("data-resource-drop-over");
    resourceTarget = target;
    target?.setAttribute("data-resource-drop-over", "true");
  }
  const start = { x: event.clientX, y: event.clientY };
  let started = false, finished = false;
  const pointerId = event.pointerId;
  const bridge = desktopBridge();
  const payload = bridge && buildTransferPayload({ kind: "tab", tabIds: [tabId] }, bridge.windowId);
  const token = payload ? bridge?.tabTransfer.prepare(payload) : undefined;
  const sessionOwner: CanvasDragOwner = { cancel: () => cancel(), release: (e) => void finish(e) };
  function cleanup(drop?: CanvasDrop | null) {
    finished = true;
    if (drop) holdOverviewLanding(drop, [tabId]);
    markResourceTarget(null);
    dragLabel?.remove(); dragLabel = null;
    pane.removeAttribute("data-pane-dragging");
    try { if (grip.hasPointerCapture(pointerId)) grip.releasePointerCapture(pointerId); } catch { /* already released */ }
    window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", finish);
    window.removeEventListener("keydown", key);
    window.removeEventListener("pointercancel", cancel); window.removeEventListener("blur", cancel);
    if (started) setCanvasDragging(false);
  }
  function cancel() {
    if (finished) return;
    cleanup();
    if (token) void bridge?.tabTransfer.cancel(token);
  }
  /** Where the drop lands. Never throws: a failed lookup means no drop. */
  function resolveDrop(x: number, y: number) {
    try {
      const resource = resourceDropTarget({ kind: "tab", tabIds: [tabId] }, x, y);
      if (resource) return { resource, drop: null, strip: null };
      const strip = tabStripDropAt(x, y);
      return { resource: null, drop: strip ? null : overviewDropAt(x, y), strip };
    } catch (error) {
      console.error("[canvas] drop lookup failed", error);
      return { resource: null, drop: null, strip: null };
    }
  }
  function show(x: number, y: number) {
    const target = resolveDrop(x, y);
    markResourceTarget(target.resource);
    if (target.resource) showResourceDrop(target.resource);
    else if (target.strip) showDropPreview(target.strip, "tab-strip");
    else showDropPreview(null);
  }
  function key(e: KeyboardEvent) {
    if (e.key === "Escape") cancel();
  }
  function move(e: PointerEvent) {
    if (e.pointerId !== pointerId) return;
    // The button came up somewhere the page could not see.
    if (e.buttons === 0 && e.pointerType !== "touch") { void finish(e); return; }
    if (!started && Math.hypot(e.clientX - start.x, e.clientY - start.y) < 6) return;
    if (!started) {
      started = true;
      // Capture only once it is a drag, so a plain press still clicks the
      // window button under it.
      try { grip.setPointerCapture(pointerId); } catch { /* the window listeners still cover the drag */ }
      setCanvasDragging(true, sessionOwner);
      // Picture the other panes before the overview covers them.
      void captureVisiblePanes([tabId]).then(() => { if (!finished) openOverview("drag", [tabId]); });
      pane.setAttribute("data-pane-dragging", "");
      dragLabel = label!.cloneNode(true) as HTMLElement;
      dragLabel.removeAttribute("hidden");
      dragLabel.removeAttribute("data-pane-drag-label");
      dragLabel.setAttribute("data-pane-drag-preview", "true");
      dragLabel.setAttribute("aria-hidden", "true");
      dragLabel.inert = true;
      Object.assign(dragLabel.style, {
        display: "flex", position: "fixed", width: "220px", height: "32px",
        minWidth: "0", maxWidth: "calc(100vw - 16px)", margin: "0", padding: "0 8px",
        zIndex: "2147483647", opacity: "1",
        ...DRAG_SURFACE_STYLE,
        pointerEvents: "none", borderRadius: "8px", transition: "none",
      });
      document.body.append(dragLabel);
    }
    if (dragLabel) Object.assign(dragLabel.style, { left: `${e.clientX - 110}px`, top: `${e.clientY - 16}px` });
    show(e.clientX, e.clientY);
  }
  async function finish(e: PointerEvent) {
    if (finished || e.pointerId !== pointerId) return;
    const target = started ? resolveDrop(e.clientX, e.clientY) : null;
    const wasStarted = started;
    cleanup(target?.drop);
    try {
      const outside = e.clientX < 0 || e.clientY < 0 || e.clientX > window.innerWidth || e.clientY > window.innerHeight;
      if (wasStarted && outside && token) { await bridge?.tabTransfer.detach(token); return; }
      const resourceSessionId = target?.resource?.dataset.resourceDropSession;
      if (resourceSessionId) { await dropWebTabResource(tabId, resourceSessionId, token ?? undefined); return; }
      if (token && !await bridge?.tabTransfer.cancel(token)) { closeOverview(); return; }
      if (target?.drop) commitCanvasDrop(target.drop, [tabId]);
      else if (target?.strip) useCenterTabs.getState().ungroupTab(tabId);
    } catch (error) {
      closeOverview();
      console.error("[canvas] pane drop failed", error);
    }
  }
  window.addEventListener("pointermove", move); window.addEventListener("pointerup", finish);
  window.addEventListener("keydown", key);
  window.addEventListener("pointercancel", cancel); window.addEventListener("blur", cancel);
}
