"use client";

/**
 * Pointer-driven tab drag (Chrome-style) — the whole engine in one hook.
 *
 * The pressed tab element itself follows the pointer via `transform`: no
 * HTML5 drag, no system ghost, no residue at the origin slot. One in-flight
 * drag lives in a ref (pointermove writes the transform directly, so there
 * is no re-render per frame); only intent changes (marker / shifts) and the
 * detach cue go through React state.
 *
 * Drop-to-place: a tab leaving the strip only shows detach intent while
 * dragging; the torn-off window is created at RELEASE.
 */
import { useLayoutEffect, useRef, useState } from "react";

import {
  DETACH_HYSTERESIS_PX,
  DRAG_START_THRESHOLD_PX,
  dragCoordinator,
  resolveTabDropIntent,
  SWAP_OVERLAP_RATIO,
  type TabDragSubject,
  type TabDropIntent,
} from "@/lib/tab-drag-coordinator";
import { buildTransferPayload, desktopBridge } from "@/lib/desktop-bridge";
import { useCenterTabs, type CenterTab } from "@/lib/state/center-tabs-store";
import { useTranslation } from "@/lib/i18n";
import {
  cancelCoordinator,
  isFourthMemberRejection,
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
} from "./tab-strip-geometry";

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
  width: number;
  minTx: number;
  maxTx: number;
  targets: PointerDropTarget[];
  /** Latest clamped offset, re-applied after each React commit. */
  lastTx: number;
  lastIntent: TabDropIntent | null;
  /** Tab strip's vertical span (viewport px), snapshotted at drag start.
   *  Detach intent triggers geometrically when the cursor leaves this band;
   *  drop-to-place, so the torn-off window is only created at release. */
  stripTop: number;
  stripBottom: number;
  teardown(): void;
}

export interface TabPointerDragOptions {
  stripRef: React.RefObject<HTMLDivElement | null>;
  tabsFlowRef: React.RefObject<HTMLDivElement | null>;
  /** Chrome activates on press: the strip's own click path runs on
   *  pointerdown so session/web/file tabs each activate the way they do
   *  on a click. */
  onTabClick(tab: CenterTab): void;
  /** Tab id activated by the current pointerdown, consumed by the click
   *  that follows it (see the strip's onTabClickFromPointer). */
  activatedOnPressRef: { current: string | null };
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

export function useTabPointerDrag({
  stripRef,
  tabsFlowRef,
  onTabClick,
  activatedOnPressRef,
  tabMenuRef,
  applyDrop,
  setDragAnnouncement,
}: TabPointerDragOptions) {
  const { text } = useTranslation();
  const [draggedIds, setDraggedIds] = useState<ReadonlySet<string>>(new Set());
  const [dropMarker, setDropMarker] = useState<TabDropIntent | null>(null);
  const [dragWidth, setDragWidth] = useState(0);
  /** True once the drag crosses the detach threshold: the tab is leaving,
   *  so the strip closes its slot (see detachShifts). */
  const [detaching, setDetaching] = useState(false);
  /** Floating "New window" cue position while detaching. Portaled outside the
   *  strip (which clips vertical overflow), tracks the pointer. Null = hidden. */
  const [detachCue, setDetachCue] = useState<{ x: number; y: number } | null>(null);
  /** True while the detach cursor is over ANOTHER OpenProgram window (a merge
   *  target). Suppresses the "New window" pill — that window shows its own
   *  "Add tab here" cue instead, so the two are mutually exclusive by location. */
  const [detachOverTarget, setDetachOverTarget] = useState(false);
  const detachHoverPollRef = useRef(false);

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
    drag.element.style.transform = `translateX(${drag.lastTx}px)`;
  });

  function clearDragState() {
    removeReleaseListener();
    setDraggedIds(new Set());
    setDropMarker(null);
    setDragWidth(0);
    setDetaching(false);
    setDetachCue(null);
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
  function restorePointerDragElement(element: HTMLElement, animateHome: boolean) {
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
    pointerDragRef.current = null;
    drag.teardown();
    restorePointerDragElement(drag.element, true);
    return drag.started;
  }

  /** Escape / pointercancel / window blur: return-home animation plus
   *  full coordinator + token + marker cleanup. Drop-to-place, so there is no
   *  mid-drag window to dispose — the tab simply slides home. */
  function cancelPointerDrag() {
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
    // Chrome activates on press, not on release: the pressed tab is live
    // for the whole drag and stays selected afterwards. Reuse the click
    // path so session/web/file tabs each activate the way they already do.
    // A group handle carries no single tab, so it does not activate.
    if (subject.kind !== "group") {
      const pressed = useCenterTabs
        .getState()
        .tabs.find((tab) => tab.id === subject.tabIds[0]);
      // Already-active tab: onTabClick would bump the session
      // re-activation request (its click-to-reload behaviour), which a
      // press must not trigger. Only activate when it actually changes.
      if (pressed && pressed.id !== useCenterTabs.getState().activeId) {
        onTabClick(pressed);
        // The click that follows this press must not re-run onTabClick:
        // it would now see the tab as already active and bump the session
        // re-activation request (click-to-reload), which a press must not do.
        activatedOnPressRef.current = pressed.id;
      }
    }
    onPrepareDrag(subject);
    const prepared = dragCoordinator.current();
    if (!prepared) return;
    const element = (subject.kind === "group"
      ? event.currentTarget.parentElement ?? event.currentTarget
      : event.currentTarget) as HTMLElement;
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
      width: 0,
      minTx: 0,
      maxTx: 0,
      targets: [],
      lastTx: 0,
      lastIntent: null,
      stripTop: 0,
      stripBottom: 0,
      teardown() {
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
      removeReleaseListener();
      // Static slot geometry snapshot — every later hit test runs
      // against these unshifted rects (bystanders only ever move via
      // transform), so nothing can oscillate under the dragged tab.
      const flow = tabsFlowRef.current;
      const unitRect = drag.element.getBoundingClientRect();
      drag.originLeft = unitRect.left;
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
      // In-strip reorder clamps the tab to the visible slot span. But once the
      // drag leaves the strip / hovers another window (detach or merge intent,
      // from last frame), the tab should track the cursor FREELY — never freeze
      // at the strip edge. Whether it actually merges is decided at release by
      // the live hit test, so free movement here costs nothing.
      // Always clamp the tab to the visible slot span — the same limit that
      // keeps it from vanishing off the edge in a plain reorder. Detach/merge
      // must NOT lift that clamp (that let the tab run out to the window edge
      // and nearly clip). Intent is shown by the floating "New window" pill and
      // the detach-intent style, never by the tab body leaving its row.
      const tx = Math.min(Math.max(dx, drag.minTx), drag.maxTx);
      drag.lastTx = tx;
      drag.element.style.transform = `translateX(${tx}px)`;
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
    // Drop-to-place: while dragging out of the strip the tab shows detach-intent
    // (translucent, accent outline) plus a floating "New window" pill near the
    // cursor. The pill is portaled outside .tabsFlow — that 40px strip clips
    // vertical overflow, so an on-tab pill above/below is invisible. No window
    // is created mid-drag; it is torn off at release (onPointerDragUp).
    drag.element.toggleAttribute("data-detach-intent", drag.detaching);
    setDetachCue(
      drag.detaching ? { x: e.clientX, y: e.clientY } : null,
    );
    // Poll the window under the cursor (same read-only hit test used at
    // release) for the ENTIRE drag once a transfer token exists — NOT only
    // while detaching. main drives each destination window's hover cue from
    // this poll and clears it on a null return, so the highlight is adaptive:
    // it follows the cursor off a window (→ null → hover-leave) and back on,
    // never latching. It also hides the source "New window" pill over a target.
    // One in-flight call at a time; no second loop. Gated on the raw token
    // (NOT detachCapable) so a SOLO window still polls — it can't detach, but
    // it must detect another window under the cursor to merge onto it.
    if (hasTransferToken && !detachHoverPollRef.current) {
      if (bridge?.tabTransfer.windowAtCursor) {
        detachHoverPollRef.current = true;
        void bridge.tabTransfer
          .windowAtCursor()
          .then((id) => {
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
    pointerDragRef.current = null;
    drag.teardown();
    if (!drag.started) return; // plain click — the once-pointerup listener releases the token
    const prepared = dragCoordinator.current();
    if (!prepared?.started) {
      restorePointerDragElement(drag.element, true);
      clearDragState();
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
      restorePointerDragElement(drag.element, true);
      const token = prepared.transferToken;
      const bridge = desktopBridge();
      const wantsDetach = drag.detaching; // geometric tear-off intent at release
      dragCoordinator.clear(); // main / the destination owns the token now
      clearDragState();
      if (!bridge || !token) return;
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
            setDragAnnouncement(text("Tab move cancelled", "标签移动已取消"));
            return;
          }
          const detachedWindowId = await bridge.tabTransfer.detach(token);
          setDragAnnouncement(
            detachedWindowId
              ? text("Tab moved to new window", "标签已移至新窗口")
              : text("Tab move cancelled", "标签移动已取消"),
          );
        } catch {
          setDragAnnouncement(text("Tab move cancelled", "标签移动已取消"));
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
    const fourthMemberRejected = isFourthMemberRejection(prepared.subject, intent);
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
      cancelDrag(!fourthMemberRejected);
      if (fourthMemberRejected) {
        setDragAnnouncement(
          text("Split supports up to three tabs", "分屏最多支持三个标签"),
        );
      }
    }
  }

  return {
    draggedIds,
    dropMarker,
    dragWidth,
    detaching,
    detachCue,
    detachOverTarget,
    onTabPointerDown,
    setDraggedIds,
    setDropMarker,
    clearDragState,
    cancelDrag,
    teardownPointerDrag,
  };
}
