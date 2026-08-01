"use client";

/**
 * CenterTabStrip — the browser-style tab row over the center column.
 *
 * Session tabs are bookmarks over the SINGLETON chat surface: clicking
 * one activates its session through the same path the left sidebar
 * uses (router.push("/s/<id>")); the chat DOM itself never remounts.
 * The session↔tab sync (activation upserts a tab, /chat with no session
 * focuses the draft tab, title changes rename tabs) plus the open/close
 * animation bookkeeping live in `./use-tab-lifecycle`; the pointer drag
 * engine in `./use-tab-pointer-drag`; the context menu and its actions in
 * `./use-tab-menu` + `./tab-context-menu`; the tab/segment rendering in
 * `./tab-items`. This file wires them together and owns the strip layout.
 *
 * Closing a tab with unsaved edits (dirty=true, set by the file
 * editor) asks for confirmation first and, on discard, drops the
 * surviving fileDrafts buffer so reopening starts clean.
 * ponytail: window.confirm — the strip has no dialog host; swap for
 * ConfirmDialog if one ever lands at this level.
 */
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { CirclePlus, Plus, SquareArrowOutUpRight } from "lucide-react";

import { useCenterTabs, type CenterTab } from "@/lib/state/center-tabs-store";
import { centerTabStripEntries } from "@/lib/state/center-tab-groups";
import { dragCoordinator } from "@/lib/tab-drag-coordinator";
import { desktopBridge } from "@/lib/desktop-bridge";
import { MainMenu } from "./main-menu";
import { SplitViewPicker } from "./split-view-picker";
import { useTranslation } from "@/lib/i18n";
import styles from "./center-tabs.module.css";
import { computeLiveShifts } from "./tab-strip-geometry";
import { cancelCoordinator, removeReleaseListener } from "./tab-drag-subject";
import { CompoundTabItem, TabItem, labelOf } from "./tab-items";
import { TabContextMenu } from "./tab-context-menu";
import { useTabDropActions } from "./use-tab-drop-actions";
import { useTabLifecycle } from "./use-tab-lifecycle";
import { useTabMenu } from "./use-tab-menu";
import { useTabPointerDrag } from "./use-tab-pointer-drag";

/** Stable empty shift map — used while detaching, when neighbours must not
 *  move to fill the leaving tab's slot. A shared frozen instance avoids a new
 *  Map every render. */
const EMPTY_SHIFTS: ReadonlyMap<string, number> = new Map();

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

  const { targetBeforeId, applyDrop, moveGroupByKeyboard } = useTabDropActions();

  // The three hooks below form a cycle (lifecycle closes → drag cancels;
  // menu acts → drag clears; drag presses → lifecycle activates), so the
  // backward edges go through refs that always hold the current render's
  // callback. The forward edges are passed directly.
  const cancelDragRef = useRef<(announce?: boolean) => void>(() => {});
  const clearDragStateRef = useRef<() => void>(() => {});
  const onTabCloseRef = useRef<(e: React.SyntheticEvent, tab: CenterTab) => void>(
    () => {},
  );
  const tabMenuRef = useRef<{ tabId: string } | null>(null);

  const {
    tabs,
    enteringIds,
    closingIds,
    activatedOnPressRef,
    onTabClick,
    onTabClickFromPointer,
    onOpenNewTab,
    onTabClose,
    finishClose,
  } = useTabLifecycle({
    cancelDrag: () => cancelDragRef.current(),
    activeId,
    setFocusedTabId,
  });
  onTabCloseRef.current = onTabClose;

  const {
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
  } = useTabPointerDrag({
    stripRef,
    tabsFlowRef,
    onTabClick,
    activatedOnPressRef,
    tabMenuRef,
    applyDrop,
    setDragAnnouncement,
  });
  cancelDragRef.current = cancelDrag;
  clearDragStateRef.current = clearDragState;

  const menu = useTabMenu({
    stripRef,
    onTabClose: (event, tab) => onTabCloseRef.current(event, tab),
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

  const [detachCueHost, setDetachCueHost] = useState<Element | null>(null);
  useEffect(() => {
    setDetachCueHost(detachCue ? document.querySelector(".center-body") : null);
  }, [detachCue !== null]);

  const stripEntries = centerTabStripEntries({
    tabIds: tabs.map((tab) => tab.id),
    groups,
  });

  // Live-reorder geometry for this render: entry id → translateX px.
  // While detaching, the dragged tab is being pulled OUT to a new window
  // but hasn't left yet — the user wants its slot to stay put (the
  // neighbours must NOT slide in to fill it), so that dragging back in
  // simply cancels with nothing to un-shift. The gap only closes once the
  // tab is actually gone (removed from stripEntries at release), which the
  // normal layout handles. So detaching contributes no shifts; only an
  // in-strip reorder (with a drop marker) moves neighbours.
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
      setDraggedIds(new Set());
      setDropMarker(null);
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
      data-transfer-hover={transferHover || undefined}
    >
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
                focusedTabId={focusedTabId}
                enteringIds={enteringIds}
                closingIds={closingIds}
                onActivate={onTabClickFromPointer}
                onFocusTab={setFocusedTabId}
                onOpenMenu={menu.openTabMenu}
                onClose={onTabClose}
                onExited={finishClose}
                shiftX={liveShifts.get(entry.id) ?? 0}
                onDragPointerDown={onTabPointerDown}
                onMoveGroup={(groupId, direction) =>
                  moveGroupByKeyboard(stripEntries, groupId, direction)
                }
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
              tabStop={tab.id === focusedTabId}
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
      >
        <Plus size={15} />
      </button>
      {/* Main menu (Chrome's ⋮) owns the reserved column at the right
         end of the strip; the ＋ above is therefore free to sit
         naturally after the last tab. */}
      <MainMenu />
      {tabMenu ? (
        <TabContextMenu
          tabMenu={tabMenu}
          tabs={tabs}
          groups={groups}
          canMoveToNewWindow={menu.canMoveToNewWindow}
          canMoveMenuTab={menu.canMoveMenuTab}
          moveMenuTab={menu.moveMenuTab}
          closableTabsAround={menu.closableTabsAround}
          closeMenuTabs={menu.closeMenuTabs}
          canOpenSplitPicker={menu.canOpenSplitPicker}
          openSplitPicker={menu.openSplitPicker}
          removeMenuTabFromGroup={menu.removeMenuTabFromGroup}
          moveMenuTabToNewWindow={menu.moveMenuTabToNewWindow}
          setTabMenu={menu.setTabMenu}
          onTabClose={onTabClose}
        />
      ) : null}
      {/* Split picker lives in the center body (the strip is a 40px band
         that would clip it), so portal it onto that surface. */}
      {splitPickerTabId && splitPickerHost
        ? createPortal(
            <SplitViewPicker
              subjectId={splitPickerTabId}
              titleOf={(tab) => labelOf(tab, t, text)}
              onClose={() => {
                const subject = splitPickerTabId;
                setSplitPickerTabId(null);
                menu.returnFocusToMenuInvoker(subject);
              }}
              onPicked={(accepted) => {
                const subject = splitPickerTabId;
                setSplitPickerTabId(null);
                setDragAnnouncement(
                  accepted
                    ? text("Tab added to split", "标签已加入分屏")
                    : text(
                        "Split supports up to three tabs",
                        "分屏最多支持三个标签",
                      ),
                );
                menu.returnFocusToMenuInvoker(subject);
              }}
            />,
            splitPickerHost,
          )
        : null}
      {/* Floating detach cue: portaled into .center-body so it escapes the
         strip's overflow clip. pointer-events:none and NOT a child of the
         captured tab, so pointer capture is untouched. */}
      {detachCue && detachCueHost && !detachOverTarget
        ? createPortal(
            <div
              className={styles.detachCue}
              style={{ left: detachCue.x, top: detachCue.y }}
              aria-hidden="true"
            >
              <SquareArrowOutUpRight size={14} />
              {text("New window", "新窗口")}
            </div>,
            detachCueHost,
          )
        : null}
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
