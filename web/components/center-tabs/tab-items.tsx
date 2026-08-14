"use client";

/**
 * Strip tab presentation — the two components the strip maps over.
 *
 * `TabItem` is one tab (or one segment inside a compound); `CompoundTabItem`
 * is a split-view group: a drag handle plus its member TabItems, with a FLIP
 * animation for member reorders. Both are pure render + local animation
 * state; every mutation is a prop callback owned by CenterTabStrip.
 */
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Bookmark, FileText, Globe, GripVertical, History, CirclePlus, TerminalSquare, X } from "lucide-react";

import {
  MessageCircleIcon,
  type AnimatedNavIconHandle,
} from "@/components/animated-icons";
import type { CenterTab } from "@/lib/state/center-tabs-store";
import type { CenterTabGroup } from "@/lib/state/center-tab-groups";
import type { TabDragSubject } from "@/lib/tab-drag-coordinator";
import { useTranslation } from "@/lib/i18n";
import { builtinPageLabel } from "./builtin-page-label";
import { shiftStyle } from "./tab-strip-geometry";
import styles from "./center-tabs.module.css";

export function labelOf(
  tab: CenterTab,
  t: ReturnType<typeof useTranslation>["t"],
  text: ReturnType<typeof useTranslation>["text"],
): string {
  if (tab.kind === "ntp") return text("New tab", "新标签页");
  if (tab.kind === "builtin") return builtinPageLabel(tab.page, text);
  if (tab.kind === "file") return tab.title;
  if (tab.kind === "web") return tab.title || tab.url || "";
  if (tab.draft) return text("New chat", "新会话");
  return tab.title || t("sidebar.untitled");
}

interface CompoundTabItemProps {
  group: CenterTabGroup;
  tabs: CenterTab[];
  activeId: string | null;
  focusedTabId: string | null;
  enteringIds: ReadonlySet<string>;
  closingIds: ReadonlySet<string>;
  onActivate(tab: CenterTab): void;
  onFocusTab(tabId: string): void;
  onOpenMenu(
    event: React.MouseEvent<HTMLElement> | React.KeyboardEvent<HTMLElement>,
    tabId: string,
  ): void;
  onClose(event: React.SyntheticEvent, tab: CenterTab): void;
  onExited(tab: CenterTab): void;
  shiftX: number;
  onDragPointerDown(
    subject: TabDragSubject,
    event: React.PointerEvent<HTMLElement>,
  ): void;
  onMoveGroup(groupId: string, direction: -1 | 1): void;
}

export function CompoundTabItem({
  group,
  tabs,
  activeId,
  focusedTabId,
  enteringIds,
  closingIds,
  onActivate,
  onFocusTab,
  onOpenMenu,
  onClose,
  onExited,
  shiftX,
  onDragPointerDown,
  onMoveGroup,
}: CompoundTabItemProps) {
  const { t, text } = useTranslation();
  const active = group.memberIds.includes(activeId ?? "");
  const closingCount = group.memberIds.filter((tabId) =>
    closingIds.has(tabId),
  ).length;
  const remainingCount = group.memberIds.length - closingCount;
  // FLIP: when members reorder within the compound (drag drop or keyboard
  // Move left/right), slide each segment from its previous offset to its
  // new one. Membership changes (enter/exit) keep their own animations.
  const rootRef = useRef<HTMLDivElement>(null);
  const segmentOffsets = useRef(new Map<string, number>());
  const previousOrder = useRef<string[]>(group.memberIds);
  const orderKey = group.memberIds.join("\0");
  useLayoutEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    const sameMembers =
      previousOrder.current.length === group.memberIds.length
      && group.memberIds.every((tabId) => previousOrder.current.includes(tabId));
    previousOrder.current = [...group.memberIds];
    const reducedMotion =
      typeof window.matchMedia === "function"
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const nextOffsets = new Map<string, number>();
    for (const child of Array.from(root.children) as HTMLElement[]) {
      const tabId = child.querySelector<HTMLElement>("[data-tab-id]")?.dataset.tabId;
      if (!tabId) continue; // group drag handle
      nextOffsets.set(tabId, child.offsetLeft);
      const previousLeft = segmentOffsets.current.get(tabId);
      if (
        sameMembers && !reducedMotion
        && previousLeft !== undefined && previousLeft !== child.offsetLeft
      ) {
        child.animate(
          [
            { transform: `translateX(${previousLeft - child.offsetLeft}px)` },
            { transform: "translateX(0)" },
          ],
          { duration: 180, easing: "ease" },
        );
      }
    }
    segmentOffsets.current = nextOffsets;
    // ponytail: keyed on member order only — resize between reorders just
    // replays from a stale offset for one 180ms slide, not worth observing.
  }, [orderKey]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div
      ref={rootRef}
      className={`${styles.compoundTab} ${active ? styles.compoundTabActive : ""}`}
      data-member-count={group.memberIds.length}
      data-closing-count={closingCount || undefined}
      // A compound mid-collapse animates its own outer width (see
      // data-closing-count in the stylesheet) — freezeStripWidths must
      // leave it alone, same as a single closing tab.
      data-tab-closing={closingCount ? true : undefined}
      data-remaining-count={remainingCount}
      style={shiftStyle(shiftX)}
      role="presentation"
    >
      <button
        type="button"
        className={styles.groupDragHandle}
        aria-label={text("Move tab group", "移动标签组")}
        title={text("Move tab group", "移动标签组")}
        onPointerDown={(event) => {
          if (event.button !== 0) return;
          event.stopPropagation();
          onDragPointerDown(
            {
              kind: "group",
              tabIds: [...group.memberIds],
              sourceGroup: group,
            },
            event,
          );
        }}
        onKeyDown={(event) => {
          if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
          event.preventDefault();
          event.stopPropagation();
          onMoveGroup(group.id, event.key === "ArrowLeft" ? -1 : 1);
        }}
      >
        <GripVertical size={10} aria-hidden="true" />
      </button>
      {group.memberIds.map((tabId) => {
        const tab = tabs.find((candidate) => candidate.id === tabId);
        if (!tab) return null;
        const memberIndex = group.memberIds.indexOf(tabId);
        return (
          <TabItem
            key={tab.id}
            tab={tab}
            active={tab.id === activeId}
            tabStop={tab.id === focusedTabId}
            enter={enteringIds.has(tab.id)}
            closing={closingIds.has(tab.id)}
            label={labelOf(tab, t, text)}
            closeLabel={text("Close tab", "关闭标签")}
            segment
            onActivate={onActivate}
            onFocusTab={onFocusTab}
            onOpenMenu={onOpenMenu}
            onClose={onClose}
            onExited={onExited}
            dragSubject={{
              kind: "segment",
              tabIds: [tab.id],
              sourceGroup: group,
              memberIndex,
            }}
            onDragPointerDown={onDragPointerDown}
          />
        );
      })}
    </div>
  );
}

/** One strip tab. A component (not map-inline JSX) so each session
 *  tab owns the ref that drives its animated icon from row hover. */
export function TabItem({
  tab,
  active,
  tabStop,
  enter,
  closing,
  label,
  closeLabel,
  segment = false,
  onActivate,
  onFocusTab,
  onOpenMenu,
  onClose,
  onExited,
  dragSubject,
  shiftX = 0,
  onDragPointerDown,
}: {
  tab: CenterTab;
  active: boolean;
  tabStop: boolean;
  enter: boolean;
  closing: boolean;
  label: string;
  closeLabel: string;
  segment?: boolean;
  onActivate: (tab: CenterTab) => void;
  onFocusTab: (tabId: string) => void;
  onOpenMenu: (
    event: React.MouseEvent<HTMLElement> | React.KeyboardEvent<HTMLElement>,
    tabId: string,
  ) => void;
  onClose: (e: React.SyntheticEvent, tab: CenterTab) => void;
  onExited: (tab: CenterTab) => void;
  dragSubject: TabDragSubject;
  shiftX?: number;
  onDragPointerDown: (
    subject: TabDragSubject,
    event: React.PointerEvent<HTMLElement>,
  ) => void;
}) {
  const iconRef = useRef<AnimatedNavIconHandle>(null);
  const tabRef = useRef<HTMLDivElement>(null);
  // 入场动画只在挂载那一次播；播完摘掉 class（.tabEnter 的 overflow:hidden
  // 不能留着，否则会裁掉活动 tab 的 fillet）。
  const [entering, setEntering] = useState(enter);
  // 记住加载失败的那个 URL（而不是一个 bool），换站点后新图标还能再试。
  const [brokenFavicon, setBrokenFavicon] = useState("");
  useEffect(() => {
    if (active) tabRef.current?.scrollIntoView({ block: "nearest", inline: "nearest" });
    if (!active || typeof ResizeObserver === "undefined") return;
    const flow = tabRef.current?.closest<HTMLElement>('[role="tablist"]');
    if (!flow) return;
    const revealActiveTab = () =>
      tabRef.current?.scrollIntoView({ block: "nearest", inline: "nearest" });
    const observer = new ResizeObserver(revealActiveTab);
    observer.observe(flow);
    return () => observer.disconnect();
  }, [active]);
  return (
    <div
      ref={tabRef}
      className={`${styles.tab} ${segment ? styles.compoundSegment : ""} ${active ? styles.tabActive : ""} ${entering ? styles.tabEnter : ""} ${closing ? styles.tabExit : ""}`}
      style={shiftStyle(shiftX)}
      // Read by freezeStripWidths: a tab mid-exit must keep its shrinking
      // width, not be pinned to the width it had when the freeze ran.
      data-tab-closing={closing || undefined}
      onAnimationEnd={(e) => {
        if (e.target !== e.currentTarget) return;
        if (closing) onExited(tab);
        else setEntering(false);
      }}
      title={tab.kind === "file" ? tab.path : tab.kind === "web" ? tab.url : label}
      onClick={() => onActivate(tab)}
      onMouseEnter={() => iconRef.current?.startAnimation?.()}
      onMouseLeave={() => iconRef.current?.stopAnimation?.()}
      // Middle-click closes (browser convention). preventDefault on
      // mousedown stops the autoscroll cursor; the close itself
      // fires on auxclick.
      onMouseDown={(e) => {
        if (e.button === 1) e.preventDefault();
      }}
      onPointerDown={(event) => onDragPointerDown(dragSubject, event)}
      onAuxClick={(e) => {
        if (e.button === 1) {
          e.preventDefault();
          onClose(e, tab);
        }
      }}
    >
      <div
        className={styles.tabTarget}
        role="tab"
        data-tab-id={tab.id}
        aria-selected={active}
        aria-label={label}
        tabIndex={tabStop ? 0 : -1}
        onFocus={() => onFocusTab(tab.id)}
        onContextMenu={(e) => onOpenMenu(e, tab.id)}
        onKeyDown={(e) => {
          if (e.shiftKey && e.key === "F10") {
            onOpenMenu(e, tab.id);
            return;
          }
          if (e.key !== "Enter" && e.key !== " ") return;
          e.preventDefault();
          onActivate(tab);
        }}
      >
        <span className={styles.tabIcon} aria-hidden="true">
          {tab.kind === "session" ? (
            <MessageCircleIcon ref={iconRef} size={14} />
          ) : tab.kind === "file" ? (
            <FileText size={13} />
          ) : tab.kind === "web" ? (
            tab.faviconUrl && tab.faviconUrl !== brokenFavicon ? (
              <img
                className={styles.tabFavicon}
                src={tab.faviconUrl}
                alt=""
                onError={() => setBrokenFavicon(tab.faviconUrl ?? "")}
              />
            ) : (
              <Globe size={13} />
            )
          ) : tab.kind === "builtin" ? (
            tab.page === "files"
              ? <FileText size={13} />
              : tab.page === "history"
              ? <History size={13} />
              : tab.page === "browser"
                ? <Globe size={13} />
                : tab.page === "terminal"
                  ? <TerminalSquare size={13} />
                  : <Bookmark size={13} />
          ) : (
            <CirclePlus size={13} />
          )}
        </span>
        <span className={styles.tabName}>{label}</span>
      </div>
      {tab.dirty ? (
        <span className={styles.tabDirtyDot} aria-hidden="true">
          {/* 8px round marker via currentColor — no text glyph */}
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              background: "currentColor",
            }}
          />
        </span>
      ) : null}
      <button
        type="button"
        className={styles.tabClose}
        aria-label={closeLabel}
        title={closeLabel}
        tabIndex={active ? 0 : -1}
        onPointerDown={(event) => event.stopPropagation()}
        onMouseDown={(event) => event.stopPropagation()}
        onClick={(event) => onClose(event, tab)}
      >
        <X size={14} />
      </button>
    </div>
  );
}
