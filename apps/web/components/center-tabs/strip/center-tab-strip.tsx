"use client";

/**
 * CenterTabStrip — the browser-style tab row over the center column.
 *
 * Session tabs are bookmarks over the SINGLETON chat surface: clicking
 * one activates its session through the same path the left sidebar
 * uses (router.push("/s/<id>")); the chat DOM itself never remounts.
 * The session↔tab sync (activation upserts a tab, /chat with no session
 * focuses the draft tab, title changes rename tabs) plus the open/close
 * animation bookkeeping live in `../tab-actions/use-tab-lifecycle`; the pointer drag
 * engine in `../tab-actions/use-tab-pointer-drag`; the context menu and its actions in
 * `../tab-actions/use-tab-menu` + `../tab-actions/tab-context-menu`; the tab/segment rendering in
 * `./tab-items`. This file wires them together and owns the strip layout.
 *
 * Closing a tab with unsaved edits (dirty=true, set by the file
 * editor) saves the document first; a failed save keeps the tab open.
 */
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { CirclePlus, LayoutGrid, Plus } from "lucide-react";

import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { topLevelTabs } from "@/lib/browser/web-tab/web-page-management";
import { PageNavigation } from "../browser/toolbar/page-navigation";
import { centerTabStripEntries } from "@/lib/tabs/center-tab-groups";
import { dragCoordinator } from "@/lib/tabs/transfer/tab-drag-coordinator";
import { desktopBridge } from "@/lib/desktop/desktop-bridge";
import { MainMenu } from "./main-menu";
import { dismissOverview, useCanvasOverview } from "@/lib/tabs/canvas/canvas-overview";
import { SplitViewPicker } from "../canvas/split-view-picker";
import { useTranslation } from "@/lib/i18n";
import styles from "./tab-strip.module.css";
import {
  computeLiveShifts,
  freezeStripWidths,
  releaseStripWidths,
} from "./tab-strip-geometry";
import { cancelCoordinator, removeReleaseListener } from "../tab-actions/tab-drag-subject";
import { CompoundTabItem, TabItem, labelOf } from "./tab-items";
import { TabContextMenu } from "../tab-actions/tab-context-menu";
import { useTabDropActions } from "../tab-actions/use-tab-drop-actions";
import { useTabLifecycle } from "../tab-actions/use-tab-lifecycle";
import { useTabMenu } from "../tab-actions/use-tab-menu";
import { useTabPointerDrag } from "../tab-actions/use-tab-pointer-drag";

/** Stable empty shift map — used while detaching, when neighbours must not
 *  move to fill the leaving tab's slot. A shared frozen instance avoids a new
 *  Map every render. */
const EMPTY_SHIFTS: ReadonlyMap<string, number> = new Map();
const EMPTY_IDS: ReadonlySet<string> = new Set();

/** Grace period between the cursor leaving the strip and the frozen tab
 *  widths being released — long enough to survive a graze along the
 *  strip's edge, short enough that a real exit reflows promptly. */
const UNFREEZE_GRACE_MS = 400;

export function CenterTabStrip() {
  const { t, text } = useTranslation();

  const groups = useCenterTabs((s) => s.groups);
  const activeId = useCenterTabs((s) => s.activeId);

  const [focusedTabId, setFocusedTabId] = useState<string | null>(activeId);
  const [dragAnnouncement, setDragAnnouncement] = useState("");
  // Cross-window drop cue (destination side): true while a drag in ANOTHER
  // window hovers this one, meaning release will merge the tab in here. Driven
  // by main via the window-at-cursor poll, mirrored through onTransferHover.
  // Confined to the TOP TAB STRIP — a page-body glow would read as "split".
  const [transferHover, setTransferHover] = useState(false);
  useEffect(() => {
    const bridge = desktopBridge();
    const sub = bridge?.tabTransfer.onTransferHover;
    if (!sub) return;
    return sub((entering) => setTransferHover(entering));
  }, []);

  const stripRef = useRef<HTMLDivElement>(null);
  const tabsFlowRef = useRef<HTMLDivElement>(null);

  const { targetBeforeId, applyDrop } = useTabDropActions();

  // ---- Chrome's close-with-the-mouse width freeze --------------------
  // Closing a tab with the × pins every survivor to its current pixel
  // width, so the next tab's × lands under the cursor that is already
  // there and a row can be closed with repeated clicks in place. The
  // widths are released when the pointer leaves the strip — with a short
  // grace period, so brushing the strip's edge (or crossing the gap
  // between two tabs, which fires mouseleave on some layouts) does not
  // reflow the row mid-click-run. Any interaction that has to relayout
  // anyway — a new tab, a drag, a window resize — releases immediately,
  // exactly as Chrome does.
  const widthFrozenRef = useRef(false);
  const unfreezeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  function releaseFrozenWidths() {
    if (unfreezeTimerRef.current !== null) {
      clearTimeout(unfreezeTimerRef.current);
      unfreezeTimerRef.current = null;
    }
    if (!widthFrozenRef.current) return;
    widthFrozenRef.current = false;
    releaseStripWidths(tabsFlowRef.current);
  }
  /** Called by the lifecycle hook for MOUSE closes only — keyboard and
   *  programmatic closes (session deleted, tab dragged out) reflow at once. */
  function freezeWidthsForMouseClose(closeTarget?: EventTarget | null) {
    if (unfreezeTimerRef.current !== null) {
      clearTimeout(unfreezeTimerRef.current);
      unfreezeTimerRef.current = null;
    }
    // Mark the closing row entry before pinning: React stamps
    // data-tab-closing on the NEXT render, after freezeStripWidths has
    // already walked the row — so resolve the × click's row entry now and
    // mark it so the pin skips it and its exit shrink runs unopposed.
    const flow = tabsFlowRef.current;
    if (flow && closeTarget instanceof HTMLElement) {
      const entry = Array.from(flow.children).find((c) =>
        c.contains(closeTarget),
      ) as HTMLElement | undefined;
      if (entry) entry.dataset.tabClosing = "true";
    }
    // Re-measure on every close: the previous freeze may predate a tab
    // that has since gone, and re-pinning the CURRENT widths is a no-op
    // for already-frozen survivors.
    freezeStripWidths(flow);
    widthFrozenRef.current = true;
  }
  useEffect(() => {
    if (typeof window === "undefined") return;
    window.addEventListener("resize", releaseFrozenWidths);
    // The strip can narrow without a window resize — sidebar toggles, a
    // split-view drag. The survivors are pinned with flex-shrink:0, so a
    // narrower row would overflow instead of adapting; any container
    // resize releases the freeze. The flow box is display:contents in
    // browser mode (no box to observe), so observe its parent strip.
    const stripBox = tabsFlowRef.current?.parentElement;
    const ro = new ResizeObserver(() => {
      if (widthFrozenRef.current) releaseFrozenWidths();
    });
    if (stripBox) ro.observe(stripBox);
    return () => {
      window.removeEventListener("resize", releaseFrozenWidths);
      ro.disconnect();
      if (unfreezeTimerRef.current !== null) clearTimeout(unfreezeTimerRef.current);
    };
    // releaseFrozenWidths only touches refs — any render's instance works.
  }, []);

  // The three hooks below form a cycle (lifecycle closes → drag cancels;
  // menu acts → drag clears; drag presses → lifecycle activates), so the
  // backward edges go through refs that always hold the current render's
  // callback. The forward edges are passed directly.
  const cancelDragRef = useRef<(announce?: boolean) => void>(() => {});
  const clearDragStateRef = useRef<() => void>(() => {});
  const tabMenuRef = useRef<{ tabId: string } | null>(null);

  const {
    tabs,
    enteringIds,
    closingIds,
    suppressedClickRef,
    onTabClickFromPointer,
    onOpenNewTab,
    onOpenOverview,
    onTabClose,
    onTabsClose,
    finishClose,
  } = useTabLifecycle({
    cancelDrag: () => cancelDragRef.current(),
    activeId,
    setFocusedTabId,
    freezeWidthsForMouseClose,
    releaseFrozenWidths,
  });
  const {
    resourceDropError,
    draggedIds,
    dropMarker,
    dragWidth,
    detaching,
    onTabPointerDown,
    clearDragState,
    cancelDrag,
    teardownPointerDrag,
  } = useTabPointerDrag({
    stripRef,
    tabsFlowRef,
    // The drag engine snapshots slot geometry at drag start and assumes it
    // stays valid, so a frozen row must be released (and reflowed) before
    // the press arms anything.
    releaseFrozenWidths,
    suppressedClickRef,
    tabMenuRef,
    applyDrop,
    setDragAnnouncement,
  });
  cancelDragRef.current = cancelDrag;
  clearDragStateRef.current = clearDragState;

  const menu = useTabMenu({
    stripRef,
    onTabsClose,
    setFocusedTabId,
    setDragAnnouncement,
    clearDragState: () => clearDragStateRef.current(),
    targetBeforeId,
  });
  // Mirror of tabMenu for the pointer handlers, which run from listeners
  // registered on an earlier render and would otherwise read a stale value.
  const tabMenu = menu.tabMenu;
  tabMenuRef.current = tabMenu;
  const { splitPickerTabId, splitPickerHost, setSplitPickerTabId } = menu;

  const overviewMode = useCanvasOverview((s) => s.mode);
  const visibleTabs = topLevelTabs(tabs, groups);
  const stripFocusId = visibleTabs.some(tab => tab.id === focusedTabId)
    ? focusedTabId : visibleTabs[0]?.id;
  // Splits formed since the last render (a merge) — not those restored
  // with the window, which are simply there.
  const prevGroupIds = useRef<ReadonlySet<string> | null>(null);
  const freshGroupIds = prevGroupIds.current === null
    ? EMPTY_IDS
    : new Set(groups.filter((group) => !prevGroupIds.current!.has(group.id)).map((group) => group.id));
  useEffect(() => {
    prevGroupIds.current = new Set(groups.map((group) => group.id));
  }, [groups]);
  const stripEntries = centerTabStripEntries({
    tabIds: visibleTabs.map((tab) => tab.id),
    groups,
  });

  // Live-reorder geometry for this render: entry id → translateX px.
  // Out of the strip the dragged tab's slot closes by width instead
  // (data-drag-out, set by the drag engine), so the tabs after it slide
  // over and the + button follows; dragging back in reopens it. So
  // detaching contributes no shifts; only an in-strip reorder (with a drop
  // marker) moves neighbours.
  const liveShifts = detaching
    ? EMPTY_SHIFTS
    : computeLiveShifts(stripEntries, draggedIds, dropMarker, dragWidth);

  function onTabListKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
    const target = (e.target as HTMLElement).closest<HTMLElement>('[role="tab"]');
    if (!target || target !== e.target || !e.currentTarget.contains(target)) return;
    const items = Array.from(
      e.currentTarget.querySelectorAll<HTMLElement>('[role="tab"]'),
    );
    const index = items.indexOf(target);
    if (index < 0 || items.length === 0) return;
    const nextIndex =
      e.key === "Home"
        ? 0
        : e.key === "End"
          ? items.length - 1
          : e.key === "ArrowRight"
            ? (index + 1) % items.length
            : (index - 1 + items.length) % items.length;
    e.preventDefault();
    setFocusedTabId(items[nextIndex].dataset.tabId ?? null);
    items[nextIndex].focus();
  }

  function onTabListWheel(e: React.WheelEvent<HTMLDivElement>) {
    if (e.currentTarget.scrollWidth <= e.currentTarget.clientWidth) return;
    if (Math.abs(e.deltaX) >= Math.abs(e.deltaY) || e.deltaY === 0) return;
    e.currentTarget.scrollLeft += e.deltaY;
    e.preventDefault();
  }

  useEffect(() => {
    const onEscape = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      const menuTabId = tabMenu?.tabId;
      const cancelled = Boolean(dragCoordinator.current() || menuTabId);
      teardownPointerDrag(); // return-home animation for a live pointer drag
      cancelCoordinator();
      removeReleaseListener();
      clearDragState();
      menu.setTabMenu(null);
      if (cancelled) {
        setDragAnnouncement(text("Tab move cancelled", "标签移动已取消"));
      }
      if (menuTabId) menu.returnFocusToMenuInvoker(menuTabId);
    };
    // Capture phase on document: the menu's own buttons hold focus while
    // it is open, and a focused native web view can swallow window-level
    // keydown entirely — Escape must reach us either way.
    document.addEventListener("keydown", onEscape, true);
    return () => {
      document.removeEventListener("keydown", onEscape, true);
      teardownPointerDrag();
      cancelCoordinator();
      removeReleaseListener();
    };
    // teardownPointerDrag only touches refs + stable setters — any render's
    // instance is equivalent, so it is deliberately not a dependency.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tabMenu, text]);

  return (
    <div
      ref={stripRef}
      className={styles.strip}
      data-center-tab-strip=""
      data-transfer-hover={transferHover || undefined}
      // Leaving the strip ends the click run → release the frozen widths
      // and let the normal rules (plus the .tab transition) reflow. The
      // delay absorbs a cursor grazing the strip's edge; re-entering
      // before it fires keeps the row pinned.
      onMouseLeave={() => {
        if (!widthFrozenRef.current || unfreezeTimerRef.current !== null) return;
        unfreezeTimerRef.current = setTimeout(
          releaseFrozenWidths,
          UNFREEZE_GRACE_MS,
        );
      }}
      onMouseEnter={() => {
        if (unfreezeTimerRef.current === null) return;
        clearTimeout(unfreezeTimerRef.current);
        unfreezeTimerRef.current = null;
      }}
    >
      <PageNavigation />
      {/* tab 流容器：浏览器模式 display:contents 零影响；桌面模式限宽，
         让＋号既跟随 tab、又最深只顶到右栏图标轴线（见 module css）。 */}
      <div
        ref={tabsFlowRef}
        className={styles.tabsFlow}
        role="tablist"
        aria-label={text("Open tabs", "打开的标签")}
        onKeyDown={onTabListKeyDown}
        onWheel={onTabListWheel}
      >
        {stripEntries.map((entry) => {
          if (entry.kind === "group") {
            return (
              <CompoundTabItem
                key={entry.id}
                group={entry.group}
                tabs={tabs}
                activeId={activeId}
                focusedTabId={stripFocusId ?? null}
                closingIds={closingIds}
                onActivate={onTabClickFromPointer}
                onFocusTab={setFocusedTabId}
                onOpenMenu={menu.openTabMenu}
                onClose={onTabsClose}
                onExited={finishClose}
                shiftX={liveShifts.get(entry.id) ?? 0}
                fresh={freshGroupIds.has(entry.group.id)}
                onDragPointerDown={onTabPointerDown}
              />
            );
          }
          const tab = tabs.find((candidate) => candidate.id === entry.tabId);
          if (!tab) return null;
          return (
            <TabItem
              key={entry.id}
              tab={tab}
              active={tab.id === activeId}
              tabStop={tab.id === stripFocusId}
              enter={enteringIds.has(tab.id)}
              closing={closingIds.has(tab.id)}
              label={labelOf(tab, t, text)}
              closeLabel={text("Close tab", "关闭标签")}
              onActivate={onTabClickFromPointer}
              onFocusTab={setFocusedTabId}
              onOpenMenu={menu.openTabMenu}
              onClose={onTabClose}
              onExited={finishClose}
              dragSubject={{ kind: "tab", tabIds: [tab.id] }}
              shiftX={liveShifts.get(entry.id) ?? 0}
              onDragPointerDown={onTabPointerDown}
            />
          );
        })}
      </div>
      <button
        type="button"
        className={styles.plusBtn}
        title={text("New tab", "新标签页")}
        aria-label={text("New tab", "新标签页")}
        onClick={onOpenNewTab}
        // ponytail: onOpenNewTab already releases (every new-tab path runs
        // through it); this stays a plain click handler.
      >
        <Plus size={16} />
      </button>
      {/* Window overview, like Safari's tab overview: every window as a
         card that keeps its split. Pinching in on the center area opens
         it too. */}
      <button
        type="button"
        className={styles.overviewBtn}
        data-overview-toggle=""
        aria-label={text("Show all windows", "显示所有窗口")}
        aria-pressed={overviewMode === "browse"}
        onClick={() => (overviewMode === "browse" ? dismissOverview() : onOpenOverview())}
      >
        <LayoutGrid size={16} />
      </button>
      {/* Main menu (Chrome's ⋮) owns the reserved column at the right
         end of the strip; the ＋ above is therefore free to sit
         naturally after the last tab. */}
      <MainMenu />
      {tabMenu ? (
        <TabContextMenu
          tabMenu={tabMenu}
          groups={groups}
          canMoveToNewWindow={menu.canMoveToNewWindow}
          canMoveMenuTab={menu.canMoveMenuTab}
          moveMenuTab={menu.moveMenuTab}
          closeMenuTab={menu.closeMenuTab}
          closableTabsAround={menu.closableTabsAround}
          closeMenuTabs={menu.closeMenuTabs}
          canOpenSplitPicker={menu.canOpenSplitPicker}
          openSplitPicker={menu.openSplitPicker}
          removeMenuTabFromGroup={menu.removeMenuTabFromGroup}
          moveMenuTabToNewWindow={menu.moveMenuTabToNewWindow}
        />
      ) : null}
      {/* Split picker lives in the center body (the strip is a 40px band
         that would clip it), so portal it onto that surface. */}
      {splitPickerTabId && splitPickerHost
        ? createPortal(
            <SplitViewPicker
              subjectId={splitPickerTabId}
              titleOf={(tab) => labelOf(tab, t, text)}
              onClose={(reason) => {
                const subject = splitPickerTabId;
                setSplitPickerTabId(null);
                if (reason !== "outside") {
                  menu.returnFocusToMenuInvoker(subject);
                }
              }}
              onPicked={(accepted) => {
                const subject = splitPickerTabId;
                setSplitPickerTabId(null);
                setDragAnnouncement(
                  accepted
                    ? text("Tab added to split", "标签已加入分屏")
                    : text(
                        "A split tab contains two views",
                        "一个分屏标签包含两个视图",
                      ),
                );
                menu.returnFocusToMenuInvoker(subject);
              }}
            />,
            splitPickerHost,
          )
        : null}
      {resourceDropError ? <div className={styles.resourceDropError} role="alert">{resourceDropError}</div> : null}
      {/* Cross-window drop cue (destination side): a drag from another window
         is hovering this one, so release merges the tab in here. Confined to
         the TOP TAB STRIP — the .strip gets a subtle accent highlight (see
         data-transfer-hover) plus this pill pinned at the strip's bottom edge.
         Rendered as a direct child of .strip (NOT inside the overflow-clipped
         .tabsFlow) and pointer-events:none so it never blocks the drop. */}
      {transferHover ? (
        <div className={styles.transferHoverPill} aria-hidden="true">
          <CirclePlus size={14} />
          {text("Drop to open here", "松开以在此打开")}
        </div>
      ) : null}
      <span className={styles.dragAnnouncement} role="status" aria-live="polite">
        {dragAnnouncement}
      </span>
    </div>
  );
}
