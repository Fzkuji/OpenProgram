"use client";

/**
 * Pointer-driven tab drag (Chrome-style) — the whole engine in one hook.
 *
 * Inside the strip the pressed tab follows the pointer via `transform`.
 * Outside it a body overlay preserves its appearance without ancestor clipping.
 * There is no HTML5 drag or visible duplicate at the origin. One in-flight
 * drag lives in a ref (pointermove writes the transform directly, so there
 * is no re-render per frame); only intent changes (marker / shifts) and the
 * detach cue go through React state.
 *
 * Drop-to-place: a tab leaving the strip only shows detach intent while
 * dragging; the torn-off window is created at RELEASE.
 */
import { useLayoutEffect, useRef, useState } from "react";
import { clearDropPreview, commitCanvasDrop, liveCanvasDropAt, showLiveCanvasDrop, showResourceDrop, setCanvasDragging } from "@/lib/tabs/canvas/canvas-drag";
import { closeOverview, holdOverviewLanding, openOverview, overviewDropAt, overviewOpen } from "@/lib/tabs/canvas/canvas-overview";
import { captureVisiblePanes, useWindowSnapshots } from "@/lib/tabs/canvas/window-snapshots";

import {
  DETACH_HYSTERESIS_PX,
  DRAG_START_THRESHOLD_PX,
  dragCoordinator,
  resolveTabDropIntent,
  SWAP_OVERLAP_RATIO,
  type TabDragSubject,
  type TabDropIntent,
} from "@/lib/tabs/transfer/tab-drag-coordinator";
import { buildTransferPayload, desktopBridge } from "@/lib/desktop/desktop-bridge";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { dropWebTabResource, resourceDropTabId, resourceDropTarget } from "@/lib/tabs/transfer/tab-resource-drop";
import { useTranslation } from "@/lib/i18n";
import { DRAG_SURFACE_STYLE } from "@/lib/prefs/appearance/visual-effects-config";
import {
  cancelCoordinator,
  isSplitCapacityRejection,
  removeReleaseListener,
  setPreparedReleaseListener,
  snapshotTabDragSubject,
} from "./tab-drag-subject";
import {
  collectPointerDropTargets,
  visibleStripBounds,
  slotOverlapRatio,
  pickPointerDropTarget,
  type PointerDropTarget,
} from "../strip/tab-strip-geometry";

/** One in-flight pointer drag. Lives in a ref — pointermove writes the
 *  dragged element's transform directly (no re-render per frame); only
 *  intent changes (marker/shifts) go through React state. */
interface PointerDragState {
  subject: TabDragSubject;
  selfIds: ReadonlySet<string>;
  element: HTMLElement;
  pointerId: number;
  startX: number;
  startY: number;
  started: boolean;
  detaching: boolean;
  /** Cursor is over another OpenProgram window (from the windowAtCursor poll).
   *  A merge target even when the geometric detach test never fired. */
  overWindow: boolean;
  /** Last cursor SCREEN position — a single-tab window follows the cursor by
   *  applying frame-to-frame screen deltas (client coords jump as it moves). */
  lastScreenX: number;
  lastScreenY: number;
  originLeft: number;
  originTop: number;
  height: number;
  preview: HTMLElement | null;
  /** The picture under the preview's tab header, once it opened as a window. */
  previewShot?: HTMLElement | null;
  previewState?: "tab" | "window" | "closing";
  width: number;
  minTx: number;
  maxTx: number;
  targets: PointerDropTarget[];
  /** Latest clamped offset, re-applied after each React commit. */
  lastTx: number;
  lastTy?: number;
  lastIntent: TabDropIntent | null;
  /** Tab strip's vertical span (viewport px), snapshotted at drag start.
   *  Detach intent triggers geometrically when the cursor leaves this band;
   *  drop-to-place, so the torn-off window is only created at release. */
  stripTop: number;
  stripBottom: number;
  teardown(): void;
}

/** The strip can clip descendants and create stacking contexts. Outside it,
 * render the same tab appearance in a body-level overlay, with the source
 * hidden. Keep the original node in place for React and pointer capture. */
function removeDragPreview(drag: PointerDragState) {
  drag.preview?.remove();
  drag.preview = null;
  drag.previewShot = null;
  drag.previewState = undefined;
  drag.element.style.visibility = "";
}

/** Outside the strip the dragged tab opens into a small window: its tab on
 *  top and the last picture of its content below, so you can see what you
 *  are carrying. Back in the strip it folds into a tab again. */
const MINI_WINDOW_WIDTH = 240;
const MINI_OPEN_MS = 200;
const MINI_CLOSE_MS = 140;
const MINI_EASE = "cubic-bezier(.2, .8, .2, 1)";

function dragPicture(drag: PointerDragState): string | null {
  const shots = useWindowSnapshots.getState().shots;
  for (const id of drag.subject.tabIds) if (shots[id]) return shots[id];
  return null;
}

/** The picture's height at the window's width: the canvas's proportions,
 *  kept within a size that still reads as a small window. */
function miniPictureHeight(): number {
  const canvas = document.querySelector<HTMLElement>("[data-canvas-root]")?.getBoundingClientRect();
  const ratio = canvas && canvas.width > 0 ? canvas.height / canvas.width : 0.62;
  return Math.round(Math.min(190, Math.max(110, MINI_WINDOW_WIDTH * ratio)));
}

function buildDragPreview(drag: PointerDragState): HTMLElement {
  const computed = getComputedStyle(drag.element);
  const preview = document.createElement("div");
  preview.setAttribute("data-tab-drag-preview", "true");
  preview.setAttribute("aria-hidden", "true");
  preview.inert = true;
  Object.assign(preview.style, {
    position: "fixed", left: `${drag.originLeft}px`, top: `${drag.originTop}px`,
    width: `${drag.width}px`, margin: "0", zIndex: "2147483647", pointerEvents: "none",
    borderRadius: computed.borderRadius, overflow: "hidden", boxSizing: "border-box",
    // Blur the backdrop while keeping the dragged label and controls opaque.
    ...DRAG_SURFACE_STYLE,
  });
  const head = drag.element.cloneNode(true) as HTMLElement;
  for (const node of [head, ...head.querySelectorAll<HTMLElement>("*")]) {
    node.removeAttribute("id");
    node.removeAttribute("data-tab-id");
    node.removeAttribute("data-tab-group-id");
  }
  Object.assign(head.style, {
    position: "relative", left: "0", top: "0", width: "100%", height: `${drag.height}px`, minWidth: "0",
    maxWidth: "none", margin: "0", transform: "none", transition: "none", opacity: "1", visibility: "visible",
    background: "transparent", boxShadow: "none", color: computed.color, font: computed.font, boxSizing: "border-box",
  });
  preview.append(head);
  return preview;
}

function openMiniWindow(drag: PointerDragState, picture: string): void {
  const preview = drag.preview!;
  let shot = drag.previewShot;
  if (!shot) {
    shot = document.createElement("div");
    Object.assign(shot.style, { height: "0px", overflow: "hidden", borderTop: "0.5px solid var(--border)" });
    const image = document.createElement("img");
    image.src = picture;
    image.alt = "";
    Object.assign(image.style, { display: "block", width: "100%", height: "100%", objectFit: "cover", objectPosition: "top center" });
    shot.append(image);
    preview.append(shot);
    drag.previewShot = shot;
  }
  const height = miniPictureHeight();
  for (const animation of [...preview.getAnimations(), ...shot.getAnimations()]) animation.cancel();
  const from = { width: preview.getBoundingClientRect().width, height: shot.getBoundingClientRect().height };
  preview.style.width = `${MINI_WINDOW_WIDTH}px`;
  preview.style.borderRadius = "10px";
  shot.style.height = `${height}px`;
  preview.animate([{ width: `${from.width}px` }, { width: `${MINI_WINDOW_WIDTH}px` }], { duration: MINI_OPEN_MS, easing: MINI_EASE });
  shot.animate([{ height: `${from.height}px` }, { height: `${height}px` }], { duration: MINI_OPEN_MS, easing: MINI_EASE });
  drag.previewState = "window";
}

function closeMiniWindow(drag: PointerDragState): void {
  const preview = drag.preview!, shot = drag.previewShot;
  drag.previewState = "closing";
  if (!shot) { removeDragPreview(drag); return; }
  for (const animation of [...preview.getAnimations(), ...shot.getAnimations()]) animation.cancel();
  const from = { width: preview.getBoundingClientRect().width, height: shot.getBoundingClientRect().height };
  preview.style.width = `${drag.width}px`;
  shot.style.height = "0px";
  preview.animate([{ width: `${from.width}px` }, { width: `${drag.width}px` }], { duration: MINI_CLOSE_MS, easing: MINI_EASE });
  const fold = shot.animate([{ height: `${from.height}px` }, { height: "0px" }], { duration: MINI_CLOSE_MS, easing: MINI_EASE });
  void fold.finished.then(() => { if (drag.preview === preview && drag.previewState === "closing") removeDragPreview(drag); }, () => undefined);
}

function updateDragPreview(drag: PointerDragState, outside: boolean) {
  if (!outside) {
    // Back over the strip: fold into a tab, then hand back to the real one.
    drag.element.removeAttribute("data-drag-out");
    if (!drag.preview) return;
    if (drag.previewState !== "closing") closeMiniWindow(drag);
    drag.preview?.style.setProperty("transform", `translate(${drag.lastTx}px, 0px)`);
    return;
  }
  if (!drag.preview) {
    drag.preview = buildDragPreview(drag);
    drag.previewState = "tab";
    document.body.append(drag.preview);
  }
  if (drag.previewState !== "window") {
    // Without any picture yet it stays a tab; one taken mid-drag opens it.
    const picture = dragPicture(drag);
    if (picture) openMiniWindow(drag, picture);
    else drag.previewState = "tab";
  }
  drag.element.style.visibility = "hidden";
  // The tab has left the strip, so its slot closes and the tabs after it
  // slide over (see .tab[data-drag-out]); back in the strip it reopens.
  drag.element.setAttribute("data-drag-out", "true");
  drag.preview.style.transform = `translate(${drag.lastTx}px, ${drag.lastTy ?? 0}px)`;
}

/** Reopen a slot closed by data-drag-out after the drag has ended, when
 *  the drag's own width transition is gone with data-pointer-drag. */
function reopenSlot(element: HTMLElement): void {
  if (!element.hasAttribute("data-drag-out")) return;
  element.setAttribute("data-slot-reopen", "true");
  void element.getBoundingClientRect(); // the transition applies before the width changes
  element.removeAttribute("data-drag-out");
  window.setTimeout(() => element.removeAttribute("data-slot-reopen"), 200);
}

export interface TabPointerDragOptions {
  stripRef: React.RefObject<HTMLDivElement | null>;
  tabsFlowRef: React.RefObject<HTMLDivElement | null>;
  /** Drop the close-run width freeze. The engine snapshots slot geometry
   *  at drag start and treats it as static, so a press must reflow the
   *  strip back to its natural widths before arming anything. */
  releaseFrozenWidths(): void;
  /** Consume the click generated by a completed or cancelled pointer drag. */
  suppressedClickRef: { current: string | null };
  /** Live mirror of the context-menu state: while it is open the first
   *  press is a dismissal and must never arm a drag. */
  tabMenuRef: { current: { tabId: string } | null };
  /** Commit an in-strip reorder intent. Returns whether the store changed. */
  applyDrop(
    prepared: NonNullable<ReturnType<typeof dragCoordinator.current>>,
    intent: TabDropIntent,
  ): boolean;
  setDragAnnouncement(message: string): void;
}

function activationTabId(subject: TabDragSubject): string | null {
  if (subject.kind !== "group") return subject.tabIds[0] ?? null;
  const tabs = useCenterTabs.getState().tabs;
  return subject.tabIds.find((id) =>
    tabs.some((tab) => tab.id === id && tab.kind === "session"),
  ) ?? (subject.tabIds.includes(subject.sourceGroup.focusedId)
    ? subject.sourceGroup.focusedId
    : subject.tabIds[0] ?? null);
}

export function useTabPointerDrag({
  stripRef,
  tabsFlowRef,
  releaseFrozenWidths,
  suppressedClickRef,
  tabMenuRef,
  applyDrop,
  setDragAnnouncement,
}: TabPointerDragOptions) {
  const { text } = useTranslation();
  const [resourceDropError, setResourceDropError] = useState("");
  const [draggedIds, setDraggedIds] = useState<ReadonlySet<string>>(new Set());
  const [dropMarker, setDropMarker] = useState<TabDropIntent | null>(null);
  const [dragWidth, setDragWidth] = useState(0);
  /** True once the drag crosses the detach threshold: the tab is leaving,
   *  so the strip closes its slot (see detachShifts). */
  const [detaching, setDetaching] = useState(false);
  /** True while the detach cursor is over ANOTHER OpenProgram window (a merge
   *  target) — that window shows its own "Add tab here" cue. */
  const [detachOverTarget, setDetachOverTarget] = useState(false);
  const detachHoverPollRef = useRef(false);

  const resourceTargetRef = useRef<HTMLElement | null>(null);
  function setResourceTarget(target: HTMLElement | null) {
    if (resourceTargetRef.current === target) return;
    resourceTargetRef.current?.removeAttribute("data-resource-drop-over");
    resourceTargetRef.current = target;
    target?.setAttribute("data-resource-drop-over", "true");
  }

  const pointerDragRef = useRef<PointerDragState | null>(null);

  // The dragged tab's transform is written imperatively on every
  // pointermove, but React owns that element's style prop and drops the
  // key on re-render (markers change several times per drag). Without
  // this the tab snaps back to its slot for a frame and then jumps to the
  // pointer again — on a fast flick the discarded offset is large, which
  // reads as the tab being flung. Re-assert it after every commit, before
  // paint, so the offset survives reconciliation.
  useLayoutEffect(() => {
    const drag = pointerDragRef.current;
    if (!drag?.started) return;
    drag.element.style.transform = `translate(${drag.lastTx}px, ${drag.lastTy ?? 0}px)`;
  });

  function clearDragState() {
    setCanvasDragging(false);
    removeReleaseListener();
    setResourceTarget(null);
    setDraggedIds(new Set());
    setDropMarker(null);
    setDragWidth(0);
    setDetaching(false);
    setDetachOverTarget(false);
  }

  function cancelDrag(announce = false) {
    cancelCoordinator();
    clearDragState();
    if (announce) setDragAnnouncement(text("Tab move cancelled", "标签移动已取消"));
  }

  function onPrepareDrag(subject: TabDragSubject) {
    removeReleaseListener();
    cancelCoordinator();
    const snapshot = snapshotTabDragSubject(subject);
    // Synchronous main-process preparation — must happen on pointer
    // down; dragstart may only read the already-prepared token.
    const bridge = desktopBridge();
    let transferToken: string | undefined;
    if (bridge) {
      const payload = buildTransferPayload(snapshot, bridge.windowId);
      transferToken =
        (payload && bridge.tabTransfer.prepare(payload)) || undefined;
    }
    dragCoordinator.prepare({
      subject: snapshot,
      transferToken,
      started: false,
      cancelled: false,
      committed: false,
    });
    const cancelUnstarted = () => {
      const prepared = dragCoordinator.current();
      if (prepared && !prepared.started) cancelCoordinator();
      removeReleaseListener();
    };
    window.addEventListener("pointerup", cancelUnstarted, { once: true });
    setPreparedReleaseListener(() =>
      window.removeEventListener("pointerup", cancelUnstarted),
    );
  }

  // ---- Pointer-driven drag (Chrome-style) --------------------------
  // The pressed tab element itself follows the pointer via transform —
  // no HTML5 drag, no system ghost, no residue at the origin slot.

  function publishDropMarker(intent: TabDropIntent | null) {
    // Only commit真正变化的 intent —— 否则每次 pointermove 都 setState
    // 重渲染，正在播的 transform transition 被打断重启。
    setDropMarker((prev) =>
      prev && intent
      && prev.mode === intent.mode
      && prev.targetTabId === intent.targetTabId
        ? prev
        : intent,
    );
  }

  /** Clear the dragged element's inline transform + drag attributes.
   *  animateHome re-enables the 160ms transition first so the tab
   *  slides back to its slot; otherwise the clear is instantaneous
   *  (the post-commit FLIP settles instead). */
  function restorePointerDragElement(element: HTMLElement, animateHome: boolean, keepSlotClosed = false) {
    if (!keepSlotClosed) reopenSlot(element);
    if (!animateHome) {
      element.style.transform = "";
      void element.getBoundingClientRect(); // flush while transitions are off
      element.removeAttribute("data-detach-intent");
      element.removeAttribute("data-pointer-drag");
      return;
    }
    element.removeAttribute("data-detach-intent");
    element.removeAttribute("data-pointer-drag");
    void element.getBoundingClientRect(); // re-enable the transition first
    element.style.transform = ""; // → 160ms slide home
  }

  /** Detach the engine from the DOM (listeners, capture)
   *  and slide the element home. Returns whether a drag had started. */
  function teardownPointerDrag(): boolean {
    const drag = pointerDragRef.current;
    if (!drag) return false;
    suppressedClickRef.current = activationTabId(drag.subject);
    pointerDragRef.current = null;
    drag.teardown();
    restorePointerDragElement(drag.element, true);
    return drag.started;
  }

  /** Escape / pointercancel / window blur: return-home animation plus
   *  full coordinator + token + marker cleanup. Drop-to-place, so there is no
   *  mid-drag window to dispose — the tab simply slides home. */
  function cancelPointerDrag() {
    const pressed = pointerDragRef.current;
    if (pressed) suppressedClickRef.current = activationTabId(pressed.subject);
    const started = teardownPointerDrag();
    cancelDrag(started);
  }

  function onTabPointerDown(
    subject: TabDragSubject,
    event: React.PointerEvent<HTMLElement>,
  ) {
    // Left button only — right/middle must never arm a drag, or the
    // context menu's own pointerdown would start one behind it.
    if (event.button !== 0 || pointerDragRef.current) return;
    // While the context menu is open, the first click is a dismissal:
    // let it through untouched so the outside-click listener sees it and
    // no drag is prepared. Dragging works normally once it is closed.
    if (tabMenuRef.current) return;
    // This press may become a drag, and the drag engine snapshots slot
    // geometry once at drag start. Release the close-run width freeze here,
    // before anything is measured, so the snapshot describes the strip the
    // user will actually see. (The × button stops propagation, so a close
    // click never reaches this handler and keeps its freeze.)
    releaseFrozenWidths();
    // A new physical press clears click suppression left by an earlier drag.
    // Keep the visible page and conversation unchanged until the ordinary click.
    suppressedClickRef.current = null;
    setResourceDropError("");
    onPrepareDrag(subject);
    const prepared = dragCoordinator.current();
    if (!prepared) return;
    const element = event.currentTarget as HTMLElement;
    element.setAttribute("data-pointer-pressed", "true");
    const pointerId = event.pointerId;
    const move = (nativeEvent: PointerEvent) => onPointerDragMove(nativeEvent);
    const up = (nativeEvent: PointerEvent) => onPointerDragUp(nativeEvent);
    const cancel = () => cancelPointerDrag();
    pointerDragRef.current = {
      subject: prepared.subject,
      selfIds: new Set(prepared.subject.tabIds),
      element,
      pointerId,
      startX: event.clientX,
      startY: event.clientY,
      started: false,
      detaching: false,
      overWindow: false,
      lastScreenX: event.screenX,
      lastScreenY: event.screenY,
      originLeft: 0,
      originTop: 0,
      height: 0,
      preview: null,
      width: 0,
      minTx: 0,
      maxTx: 0,
      targets: [],
      lastTx: 0,
      lastIntent: null,
      stripTop: 0,
      stripBottom: 0,
      teardown() {
        removeDragPreview(this);
        element.removeAttribute("data-pointer-pressed");
        setResourceTarget(null);
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        window.removeEventListener("pointercancel", cancel);
        window.removeEventListener("blur", cancel);
        try {
          element.releasePointerCapture(pointerId);
        } catch {
          /* never captured */
        }
      },
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", cancel);
    window.addEventListener("blur", cancel);
  }

  function onPointerDragMove(e: PointerEvent) {
    const drag = pointerDragRef.current;
    if (!drag || e.pointerId !== drag.pointerId) return;
    const dx = e.clientX - drag.startX;
    const dy = e.clientY - drag.startY;
    if (!drag.started) {
      if (Math.hypot(dx, dy) < DRAG_START_THRESHOLD_PX) return;
      if (!dragCoordinator.start()) {
        pointerDragRef.current = null;
        drag.teardown();
        return;
      }
      drag.started = true;
      // Pointer capture still produces a click after pointerup. Consume that
      // click once a real drag starts so reordering has no activation side
      // effect. For a composite, use the same canonical member as its one tab.
      suppressedClickRef.current = activationTabId(drag.subject);
      removeReleaseListener();
      // Static slot geometry snapshot — every later hit test runs
      // against these unshifted rects (bystanders only ever move via
      // transform), so nothing can oscillate under the dragged tab.
      const flow = tabsFlowRef.current;
      const unitRect = drag.element.getBoundingClientRect();
      drag.originLeft = unitRect.left;
      drag.originTop = unitRect.top;
      drag.height = unitRect.height;
      drag.width = unitRect.width;
      drag.targets = flow ? collectPointerDropTargets(flow) : [];
      // Clamp the dragged tab's BODY to the strip's visible span, so it
      // always stays fully on screen — never clipped by the window edge
      // and never over the traffic lights. The bound is the VISIBLE box
      // (the flow can scroll horizontally; its content width is larger).
      // In browser mode .tabsFlow is display:contents and has no box of
      // its own, so fall back to the strip's padded content box.
      const bounds = visibleStripBounds(flow, stripRef.current);
      drag.minTx = bounds ? bounds.left - unitRect.left : -Infinity;
      drag.maxTx = bounds
        ? Math.max(bounds.left - unitRect.left, bounds.right - unitRect.right)
        : Infinity;
      // Vertical strip band for the geometric detach trigger. Prefer the
      // strip container's box; fall back to the dragged unit's own rect.
      const stripRect = (stripRef.current ?? drag.element).getBoundingClientRect();
      drag.stripTop = stripRect.top;
      drag.stripBottom = stripRect.bottom;
      drag.element.setAttribute("data-pointer-drag", "true");
      // Registered so a release the page never saw (over the window-drag
      // strip or a native view) still ends this drag on the next move.
      setCanvasDragging(true, { cancel: cancelPointerDrag, release: onPointerDragUp });
      // The dragged tab's own pane is pictured too: its small window shows
      // it. An overview already open (its button, or a pinch) takes the drag.
      const inOverview = overviewOpen();
      void captureVisiblePanes().then(() => {
        if (inOverview && pointerDragRef.current === drag && overviewOpen()) openOverview("drag", drag.subject.tabIds);
      });
      try {
        drag.element.setPointerCapture(drag.pointerId);
      } catch {
        /* capture unavailable — the window listeners still cover the drag */
      }
      setDragWidth(unitRect.width);
      setDraggedIds(new Set(drag.subject.tabIds));
      setDragAnnouncement(text("Dragging tab", "正在拖动标签"));
    }
    // Single-tab window: dragging the lone tab moves the WHOLE window (nothing
    // to reorder). Move via main-process IPC using frame-to-frame SCREEN
    // deltas (client coords jump when the window itself moves). This coexists
    // with merge: the moment the cursor is over ANOTHER window (overWindow,
    // set by the poll below) we stop moving and let release deliver the merge.
    const bridge = desktopBridge();
    const hasTransferToken = Boolean(dragCoordinator.current()?.transferToken);
    const isSoloWindow =
      Boolean(bridge?.moveWindowBy) && useCenterTabs.getState().tabs.length === 1;
    if (isSoloWindow) {
      // The lone tab NEVER moves relative to its strip — the WINDOW moves under
      // it. Move the window every frame, even while hovering another window:
      // stopping mid-drag (to "prepare a merge") produced a visible stutter.
      // Whether it merges is decided at release by the live hit test, so the
      // window can keep tracking the cursor right up to release. Tab stays at 0.
      bridge!.moveWindowBy!(
        e.screenX - drag.lastScreenX,
        e.screenY - drag.lastScreenY,
      );
      drag.lastScreenX = e.screenX;
      drag.lastScreenY = e.screenY;
      drag.lastTx = 0;
      drag.element.style.transform = "translateX(0)";
    } else {
      // Inside the strip band the tab is clamped to the visible slot span (plain
      // reorder). Once the cursor leaves the band the tab itself follows the
      // pointer freely in both axes — canvas docking, Resources, new window.
      // A body overlay keeps that same tab visible outside clipping ancestors.
      const outsideBand = e.clientY < drag.stripTop || e.clientY > drag.stripBottom;
      const tx = outsideBand ? dx : Math.min(Math.max(dx, drag.minTx), drag.maxTx);
      drag.lastTy = outsideBand ? dy : 0;
      drag.lastTx = tx;
      // Leaving the strip keeps the window on screen: the tab can dock
      // beside it there (liveCanvasDropAt). The overview opens only from
      // its button or a pinch.
      updateDragPreview(drag, outsideBand);
      drag.element.style.transform = `translate(${tx}px, ${drag.lastTy}px)`;
    }

    const resourceTarget = resourceDropTarget(drag.subject, e.clientX, e.clientY);
    setResourceTarget(resourceTarget);
    if (resourceTarget) {
      showResourceDrop(resourceTarget);
      drag.detaching = false;
      drag.overWindow = false;
      drag.lastIntent = null;
      setDetaching(false);
      setDetachOverTarget(false);
      drag.element.removeAttribute("data-detach-intent");
      publishDropMarker(null);
      return;
    }

    const canvasDrop = overviewDropAt(e.clientX, e.clientY) ?? liveCanvasDropAt(e.clientX, e.clientY, drag.subject.tabIds);
    // The live cue moves between halves; the overview draws its own.
    if (canvasDrop && !overviewOpen()) showLiveCanvasDrop(canvasDrop);
    else clearDropPreview();
    if (canvasDrop) {
      drag.detaching = false; drag.overWindow = false; drag.lastIntent = null;
      setDetaching(false); publishDropMarker(null);
      return;
    }

    // Detach: cursor left the strip's vertical band (needs a desktop
    // transfer token) — Chrome has no distance dead-zone, so the trigger is
    // purely geometric. Small hysteresis so a cursor on the edge does not
    // thrash: enter detach when clearly below the bottom (or above the top),
    // come home only when clearly back inside the band.
    // A single-tab window never tears its lone tab into a NEW empty window
    // (the user was explicit: one tab, no new window). It only ever moves the
    // window (above) or merges onto another window (overWindow). So detach is
    // gated off whenever this is a solo window.
    const detachCapable = hasTransferToken && !isSoloWindow;
    // Asymmetric hysteresis: ENTER detach only after the cursor clears the
    // strip edge by a full tab-height (so the slot doesn't close on a small
    // twitch), but CANCEL (come home) the moment the cursor is back inside
    // the strip rectangle — a symmetric inner band this wide is impossible
    // on a ~40px strip. This makes "drag out to detach, drag back in to
    // cancel" work at the strip edge, not one tab-height inside it.
    const belowStrip = e.clientY > drag.stripBottom + DETACH_HYSTERESIS_PX;
    const aboveStrip = e.clientY < drag.stripTop - DETACH_HYSTERESIS_PX;
    const insideBand =
      e.clientY <= drag.stripBottom && e.clientY >= drag.stripTop;
    let nextDetaching = drag.detaching;
    if (detachCapable && (belowStrip || aboveStrip)) nextDetaching = true;
    else if (insideBand) {
      nextDetaching = false;
      // Dragging back INTO the strip cancels the tear-off. Clear the
      // cross-window intent here too — otherwise `overWindow`, which only
      // clears on the next async hit-test poll, can lag and make release
      // (`drag.detaching || drag.overWindow`) still detach/merge even
      // though the cursor is home. Synchronising it with come-home makes
      // "drag out to detach, drag back in to cancel" reliable.
      drag.overWindow = false;
      setDetachOverTarget(false);
    }
    if (nextDetaching !== drag.detaching) setDetaching(nextDetaching);
    drag.detaching = nextDetaching;
    // Drop-to-place: the dragged tab itself follows the cursor out of the
    // strip and carries the detach-intent state; there is no separate
    // floating cue. No window is created mid-drag; it is torn off at release.
    drag.element.toggleAttribute("data-detach-intent", drag.detaching);
    // Poll the window under the cursor (same read-only hit test used at
    // release) for the ENTIRE drag once a transfer token exists — NOT only
    // while detaching. main drives each destination window's hover cue from
    // this poll and clears it on a null return, so the highlight is adaptive:
    // it follows the cursor off a window (→ null → hover-leave) and back on,
    // never latching.
    // One in-flight call at a time; no second loop. Gated on the raw token
    // (NOT detachCapable) so a SOLO window still polls — it can't detach, but
    // it must detect another window under the cursor to merge onto it.
    if (hasTransferToken && !detachHoverPollRef.current) {
      if (bridge?.tabTransfer.windowAtCursor) {
        detachHoverPollRef.current = true;
        void bridge.tabTransfer
          .windowAtCursor()
          .then((id) => {
            if (pointerDragRef.current !== drag || resourceTargetRef.current) return;
            const over = id !== null;
            setDetachOverTarget(over);
            // Cursor over ANOTHER OpenProgram window ⇒ this is a merge, even
            // when its top strip sits at the same Y as our own strip band (so
            // the geometric below/above test never fired). Record it on the
            // drag so release delivers instead of committing an in-strip
            // reorder — "drag onto another window → merge".
            drag.overWindow = over;
          })
          .catch(() => {})
          .finally(() => {
            detachHoverPollRef.current = false;
          });
      }
    }
    if (drag.detaching) {
      drag.lastIntent = null;
      publishDropMarker(null);
      return;
    }
    if (detachOverTarget) setDetachOverTarget(false);

    // In-strip dragging is PURE REORDER. A neighbour yields as soon as the
    // dragged tab covers HALF of it — measured as overlap ÷ neighbour
    // width, so unequal tab widths behave correctly (for equal widths this
    // is exactly "the dragged tab's leading edge passed the neighbour's
    // midpoint"). Splitting is a context-menu action, never a drag outcome.
    const draggedRect = { left: drag.originLeft + drag.lastTx, width: drag.width };
    const centerX = draggedRect.left + drag.width / 2;
    const selfIndex = drag.targets.findIndex((slot) =>
      drag.selfIds.has(slot.tabId),
    );

    // Walk outward from the dragged tab's own slot in the travel direction
    // and take the FARTHEST neighbour that is already half-covered. That
    // makes a fast flick cross several tabs in one move, and re-deriving
    // it from scratch every frame keeps the result stable (no oscillation:
    // the answer depends only on the current position, not on history).
    let swapTarget: PointerDropTarget | null = null;
    let swapMode: "before" | "after" | null = null;
    if (selfIndex >= 0) {
      // Scan ALL neighbours on each side, never stopping at the first
      // uncovered one: after travelling past a tab its overlap drops back
      // below the threshold, so an early break would pin the marker to the
      // nearest neighbour and leave every tab beyond it un-shifted.
      for (let i = drag.targets.length - 1; i > selfIndex; i--) {
        if (slotOverlapRatio(drag.targets[i], draggedRect) >= SWAP_OVERLAP_RATIO) {
          swapTarget = drag.targets[i];
          swapMode = "after";
          break;
        }
      }
      if (!swapTarget) {
        for (let i = 0; i < selfIndex; i++) {
          if (slotOverlapRatio(drag.targets[i], draggedRect) >= SWAP_OVERLAP_RATIO) {
            swapTarget = drag.targets[i];
            swapMode = "before";
            break;
          }
        }
      }
    }
    if (swapTarget && swapMode) {
      const intent: TabDropIntent = {
        mode: swapMode,
        targetTabId: swapTarget.tabId,
      };
      drag.lastIntent = intent;
      publishDropMarker(intent);
      return;
    }
    // Covering no neighbour by half. While travelling this happens between
    // every pair of slots (leaving one before reaching the next), so HOLD
    // the last intent — clearing it would collapse every bystander back to
    // its slot for a frame and flicker. Only a drag still in its own slot
    // (never moved far enough to swap) genuinely has no intent.
    if (selfIndex >= 0) {
      const home =
        slotOverlapRatio(drag.targets[selfIndex], draggedRect)
          >= SWAP_OVERLAP_RATIO;
      if (home) {
        drag.lastIntent = null;
        publishDropMarker(null);
      } else {
        publishDropMarker(drag.lastIntent);
      }
      return;
    }
    const target = pickPointerDropTarget(drag.targets, centerX);
    if (!target) {
      publishDropMarker(null);
      return;
    }
    const intent = resolveTabDropIntent(target, centerX, target);
    drag.lastIntent = intent;
    publishDropMarker(intent);
  }

  function onPointerDragUp(e: PointerEvent) {
    const drag = pointerDragRef.current;
    if (!drag || e.pointerId !== drag.pointerId) return;
    const resourceTarget = drag.started ? resourceDropTarget(drag.subject, e.clientX, e.clientY) : null;
    pointerDragRef.current = null;
    drag.teardown();
    if (!drag.started) return; // plain click — the once-pointerup listener releases the token
    const prepared = dragCoordinator.current();
    if (!prepared?.started) {
      restorePointerDragElement(drag.element, true);
      clearDragState();
      return;
    }
    const canvasDrop = resourceTarget ? null : overviewDropAt(e.clientX, e.clientY) ?? liveCanvasDropAt(e.clientX, e.clientY, prepared.subject.tabIds);
    if (canvasDrop) {
      // The slot stays closed while the drop commits: a merged tab leaves
      // the strip, and reopening first would flash its slot back for the
      // frames until the store update lands. A tab still in the strip
      // afterwards (dropped on empty space, or a failed drop) reopens.
      const element = drag.element;
      const reopen = () => requestAnimationFrame(() => reopenSlot(element));
      restorePointerDragElement(element, false, true);
      holdOverviewLanding(canvasDrop, prepared.subject.tabIds);
      const committed = dragCoordinator.commit();
      clearDragState();
      if (!committed) { closeOverview(); reopen(); return; }
      void (async () => {
        if (committed.transferToken && !await desktopBridge()?.tabTransfer.cancel(committed.transferToken)) { closeOverview(); reopen(); return; }
        commitCanvasDrop(canvasDrop, committed.subject.tabIds);
        reopen(); // after the commit's render: only a tab still in the strip has a slot
      })();
      return;
    }
    const resourceTabId = resourceDropTabId(drag.subject);
    if (resourceTarget && resourceTabId) {
      const sessionId = resourceTarget.dataset.resourceDropSession!;
      restorePointerDragElement(drag.element, true);
      dragCoordinator.clear();
      clearDragState();
      // Main locks native records at prepare. Await its cancellation before
      // asking the registered renderer to inspect the existing Page.
      void dropWebTabResource(resourceTabId, sessionId, prepared.transferToken).then(success => {
        if (success) {
          setDragAnnouncement(text("Webpage added to Resources", "网页已添加到资源"));
          return;
        }
        const message = text("Could not attach webpage. Try again.", "网页添加失败，请重试。");
        setDragAnnouncement(message);
        setResourceDropError(message);
      });
      return;
    }
    // Drop-to-place: the tab is torn off at RELEASE, not mid-drag. Released
    // outside the strip → deliver into the window under the cursor, else
    // detach into a new window created at the drop point. overWindow covers
    // the "dragged onto another window's top strip" case where the cursor
    // never left our own strip's Y band so the geometric test stayed false.
    // Cross-window outcome (merge or detach). Enter whenever this drag COULD
    // leave the window — geometrically detaching, or the poll saw another
    // window (overWindow). But the poll is latched/async, so the FINAL outcome
    // is decided by a fresh windowAtCursor at release, not by that stale flag:
    // dragging over a window and then away must NOT merge.
    if (drag.detaching || drag.overWindow) {
      // The slot stays closed while the tab leaves for another window; it
      // reopens only if the move ends up cancelled.
      const element = drag.element;
      const reopen = () => reopenSlot(element);
      restorePointerDragElement(element, true, true);
      const token = prepared.transferToken;
      const bridge = desktopBridge();
      const wantsDetach = drag.detaching; // geometric tear-off intent at release
      dragCoordinator.clear(); // main / the destination owns the token now
      clearDragState();
      if (!bridge || !token) { reopen(); return; }
      void (async () => {
        try {
          // Live hit test at the instant of release — the single source of
          // truth for "is the cursor over another window right now?".
          const targetWindowId =
            (await bridge.tabTransfer.windowAtCursor?.()) ?? null;
          if (
            targetWindowId
            && (await bridge.tabTransfer.deliver?.(token, targetWindowId))
          ) {
            setDragAnnouncement(text("Tab moved", "标签已移动"));
            return;
          }
          // No window under the cursor. Only a real geometric tear-off spawns
          // a new window; a drag that merely hovered a window and moved away
          // (or a solo window) just releases the token and snaps back.
          if (!wantsDetach) {
            void bridge.tabTransfer.cancel?.(token);
            reopen();
            setDragAnnouncement(text("Tab move cancelled", "标签移动已取消"));
            return;
          }
          const detachedWindowId = await bridge.tabTransfer.detach(token);
          if (!detachedWindowId) reopen();
          setDragAnnouncement(
            detachedWindowId
              ? text("Tab moved to new window", "标签已移至新窗口")
              : text("Tab move cancelled", "标签移动已取消"),
          );
        } catch {
          reopen();
          setDragAnnouncement(text("Tab move cancelled", "标签移动已取消"));
        } finally {
          // A tab that moved away is gone from this strip by now; one that
          // is somehow still here must not keep a closed slot.
          window.setTimeout(() => { if (element.isConnected) reopen(); }, 1500);
        }
      })();
      return;
    }
    // In-strip release: commit the live reorder intent, then FLIP-settle
    // into the final slot.
    const intent = drag.lastIntent;
    if (!intent) {
      restorePointerDragElement(drag.element, true);
      cancelDrag(true);
      return;
    }
    const splitCapacityRejected = isSplitCapacityRejection(prepared.subject, intent);
    const beforeRect = drag.element.getBoundingClientRect();
    if (applyDrop(prepared, intent)) {
      const committed = dragCoordinator.commit();
      if (committed?.transferToken) {
        // Same-window move — release the unused main-process token.
        void desktopBridge()?.tabTransfer.cancel(committed.transferToken);
      }
      restorePointerDragElement(drag.element, false);
      const element = drag.element;
      const reducedMotion =
        typeof window.matchMedia === "function"
        && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      requestAnimationFrame(() => {
        const settle = beforeRect.left - element.getBoundingClientRect().left;
        if (settle && !reducedMotion) {
          element.animate(
            [
              { transform: `translateX(${settle}px)` },
              { transform: "translateX(0)" },
            ],
            { duration: 160, easing: "ease" },
          );
        }
      });
      setDragAnnouncement(text("Tab reordered", "标签顺序已调整"));
      clearDragState();
    } else {
      restorePointerDragElement(drag.element, true);
      cancelDrag(!splitCapacityRejected);
      if (splitCapacityRejected) {
        setDragAnnouncement(
          text("A split tab contains two views", "一个分屏标签包含两个视图"),
        );
      }
    }
  }

  return {
    resourceDropError,
    draggedIds,
    dropMarker,
    dragWidth,
    detaching,
    detachOverTarget,
    onTabPointerDown,
    setDraggedIds,
    setDropMarker,
    clearDragState,
    cancelDrag,
    teardownPointerDrag,
  };
}
