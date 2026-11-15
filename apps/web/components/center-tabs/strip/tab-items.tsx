"use client";

import { FileTypeIcon } from "@/components/files/file-type-icon";
import { ApplicationTabGlyph } from "@/components/applications/application-glyph";

/**
 * Strip tab presentation — the two components the strip maps over.
 *
 * `TabItem` is one ordinary top-level tab. `CompoundTabItem` is one top-level
 * tab whose content surface contains a split-view group. Split members are
 * not independently selectable from the strip.
 */
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Bookmark, CircleDot, CirclePlus, Download, FilePen, FileText, History, Mic, TerminalSquare, X } from "lucide-react";

import {
  ChromeIcon,
  FeatherIcon,
  MessageCircleIcon,
  type AnimatedNavIconHandle,
} from "@/components/animated-icons";
import type { CenterTab } from "@/lib/tabs/center-tabs-store";
import type { CenterTabGroup } from "@/lib/tabs/center-tab-groups";
import { rowLayout, type LayoutNode } from "@/lib/tabs/canvas/canvas-layout";
import type { TabDragSubject } from "@/lib/tabs/transfer/tab-drag-coordinator";
import { useTranslation } from "@/lib/i18n";
import { localPathFromUrl } from "@/lib/files/local-file-url";
import { labelOf } from "./tab-label";
export { labelOf } from "./tab-label";
import { routePageIcon } from "../pages/route-page-icon";
import { shiftStyle } from "./tab-strip-geometry";
import { TabFavicon } from "./tab-favicon";
import styles from "./tab-strip.module.css";

interface CompoundTabItemProps {
  group: CenterTabGroup;
  tabs: CenterTab[];
  activeId: string | null;
  focusedTabId: string | null;
  closingIds: ReadonlySet<string>;
  onActivate(tab: CenterTab): void;
  onFocusTab(tabId: string): void;
  onOpenMenu(
    event: React.MouseEvent<HTMLElement> | React.KeyboardEvent<HTMLElement>,
    tabId: string,
  ): void;
  onClose(event: React.SyntheticEvent, tabs: CenterTab[]): void;
  onExited(tab: CenterTab): void;
  shiftX: number;
  /** The split was just formed in this window (a merge): its name arrives
   *  with the same motion as a change of the pane it names. */
  fresh?: boolean;
  onDragPointerDown(
    subject: TabDragSubject,
    event: React.PointerEvent<HTMLElement>,
  ): void;
}


/** A web tab's address as a reader would type it: a local page by its
 *  path, anything else with its escapes (%E4%BA%A4…) turned back into text. */
function readableUrl(url: string | undefined): string | undefined {
  if (!url) return url;
  const local = localPathFromUrl(url);
  if (local) return local;
  try {
    return decodeURI(url);
  } catch {
    return url;
  }
}

export function CompoundMemberIcon({ tab, animate }: { tab: CenterTab; animate: boolean }) {
  const iconRef = useRef<AnimatedNavIconHandle>(null);
  useEffect(() => {
    if (animate) iconRef.current?.startAnimation?.();
    else iconRef.current?.stopAnimation?.();
  }, [animate]);
  return (
    <span className={styles.tabIcon} aria-hidden="true">
      {routePageIcon(tab.navigationRoute, 13) ?? (tab.kind === "session" ? (
        <MessageCircleIcon ref={iconRef} size={14} />
      ) : tab.kind === "application" ? (
        <ApplicationTabGlyph appId={tab.applicationId} size={14} />
      ) : tab.kind === "file" ? (
        <FileTypeIcon name={tab.path ?? ""} size={14} />
      ) : tab.kind === "web" ? (
        <TabFavicon url={tab.faviconUrl} />
      ) : tab.kind === "builtin" ? (
        tab.page === "files"
          ? <FileText size={14} />
          : tab.page === "review"
          ? <FeatherIcon size={14} />
          : tab.page === "history"
          ? <History size={14} />
          : tab.page === "downloads"
            ? <Download size={14} />
          : tab.page === "office"
            ? <FilePen size={14} />
          : tab.page === "recording"
            ? <CircleDot size={14} />
          : tab.page === "meeting"
            ? <Mic size={14} />
          : tab.page === "browser"
            ? <ChromeIcon size={14} />
            : tab.page === "terminal" || tab.page === "claude"
              ? <TerminalSquare size={14} />
              : <Bookmark size={14} />
      ) : (
        <CirclePlus size={14} />
      ))}
    </span>
  );
}

/** The split's shape in miniature, its panes laid out as on screen; the
 *  pane of the tab the strip names is the filled one. */
function SplitGlyph({ root, current }: { root: LayoutNode; current: string }) {
  const W = 16, H = 11, GAP = 1.5;
  const rects: Array<{ x: number; y: number; w: number; h: number; on: boolean }> = [];
  const walk = (node: LayoutNode, x: number, y: number, w: number, h: number) => {
    if (node.kind === "pane") { rects.push({ x, y, w, h, on: node.content === current }); return; }
    const total = node.sizes.reduce((sum, size) => sum + size, 0) || 1;
    const span = (node.dir === "row" ? w : h) - GAP * (node.children.length - 1);
    let at = node.dir === "row" ? x : y;
    node.children.forEach((child, i) => {
      const size = span * (node.sizes[i] ?? 1) / total;
      if (node.dir === "row") walk(child, at, y, size, h); else walk(child, x, at, w, size);
      at += size + GAP;
    });
  };
  walk(root, 0, 0, W, H);
  return (
    <svg className={`${styles.splitGlyph} w-[16px] h-[11px]`} viewBox={`0 0 ${W} ${H}`} aria-hidden="true">
      {rects.map((r, i) => <rect key={i} x={r.x} y={r.y} width={Math.max(0, r.w)} height={Math.max(0, r.h)} rx={1.2} data-on={r.on || undefined} />)}
    </svg>
  );
}

/** A split window is one tab, like Chrome's and Arc's split views: it
 *  names the pane in focus, and a miniature of the split with the number
 *  of panes stands for the rest (the overview shows them all). Its close
 *  closes the whole window; a single pane closes from its own grip. */
export function CompoundTabItem({
  group,
  tabs,
  activeId,
  focusedTabId,
  closingIds,
  onActivate,
  onFocusTab,
  onOpenMenu,
  onClose,
  onExited,
  shiftX,
  fresh = false,
  onDragPointerDown,
}: CompoundTabItemProps) {
  const { t, text } = useTranslation();
  const [hovered, setHovered] = useState(false);
  const nameRef = useRef<HTMLSpanElement>(null);
  const active = group.memberIds.includes(activeId ?? "");
  const memberTabs = group.memberIds.flatMap((tabId) => {
    const tab = tabs.find((candidate) => candidate.id === tabId);
    return tab ? [tab] : [];
  });
  const canonicalTab = memberTabs.find((tab) => tab.id === group.focusedId)
    ?? memberTabs.find((tab) => tab.kind === "session")
    ?? memberTabs[0];
  // The tab names one pane. When that pane changes (another pane takes
  // focus, or a merge forms the split) the new name and icon rise in, so
  // the swap reads as a change rather than a different tab appearing.
  const shownId = useRef<string | null>(fresh ? null : canonicalTab?.id ?? null);
  useLayoutEffect(() => {
    const id = canonicalTab?.id ?? null;
    if (shownId.current === id) return;
    shownId.current = id;
    const name = nameRef.current;
    if (!name || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    for (const el of [name.previousElementSibling, name]) {
      el?.animate([{ opacity: 0, transform: "translateY(5px)" }, { opacity: 1, transform: "none" }], { duration: 200, easing: "cubic-bezier(.2, .8, .2, 1)" });
    }
  });
  // One pane closing leaves the tab as it is and is removed at once
  // (onTabsClose); only the whole split closing plays the exit.
  const closingTabs = memberTabs.filter((tab) => closingIds.has(tab.id));
  const closing = closingTabs.length > 0 && closingTabs.length === memberTabs.length;
  if (!canonicalTab) return null;
  const combinedLabel = memberTabs.map((tab) => labelOf(tab, t, text)).join(" · ");
  const label = labelOf(canonicalTab, t, text);
  const closeLabel = text(`Close split window (${memberTabs.length})`, `关闭分屏窗口（${memberTabs.length} 个）`);
  const tabStop = group.memberIds.includes(focusedTabId ?? "");
  return (
    <div
      className={`${styles.compoundTab} ${active ? styles.compoundTabActive : ""} ${closing ? styles.tabExit : ""}`}
      data-member-count={group.memberIds.length}
      data-tab-closing={closing || undefined}
      style={shiftStyle(shiftX)}
      role="presentation"
      onClick={() => onActivate(canonicalTab)}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onContextMenu={(event) => onOpenMenu(event, canonicalTab.id)}
      onPointerDown={(event) => onDragPointerDown({
        kind: "group",
        tabIds: [...group.memberIds],
        sourceGroup: group,
      }, event)}
      onMouseDown={(event) => {
        if (event.button === 1) event.preventDefault();
      }}
      onAuxClick={(event) => {
        if (event.button !== 1) return;
        event.preventDefault();
        onClose(event, memberTabs);
      }}
      onAnimationEnd={(event) => {
        if (event.target !== event.currentTarget || !closing) return;
        closingTabs.forEach(onExited);
      }}
    >
      <div
        className={styles.compoundTarget}
        role="tab"
        data-tab-id={canonicalTab.id}
        aria-selected={active}
        aria-label={`${text("Split tab", "分屏标签")}: ${combinedLabel}`}
        tabIndex={tabStop ? 0 : -1}
        onFocus={() => onFocusTab(canonicalTab.id)}
        onKeyDown={(event) => {
          if (event.shiftKey && event.key === "F10") {
            onOpenMenu(event, canonicalTab.id);
            return;
          }
          if (event.key !== "Enter" && event.key !== " ") return;
          event.preventDefault();
          onActivate(canonicalTab);
        }}
      />
      <div className={styles.compoundMembers}>
        <span className={styles.compoundMember}>
          <CompoundMemberIcon tab={canonicalTab} animate={hovered} />
          <span ref={nameRef} className={styles.compoundMemberName} aria-hidden="true">{label}</span>
          {memberTabs.some((tab) => tab.dirty) ? (
            <span className={styles.tabDirtyDot} aria-hidden="true">
              <span style={{ width: 8, height: 8, borderRadius: "50%", background: "currentColor" }} />
            </span>
          ) : null}
          <span className={styles.splitMark} aria-hidden="true">
            <SplitGlyph root={group.canvas?.root ?? rowLayout(group.memberIds).root} current={canonicalTab.id} />
            {memberTabs.length}
          </span>
          <button
            type="button"
            className={`${styles.tabClose} ${styles.compoundMemberClose}`}
            aria-label={closeLabel}
            tabIndex={active ? 0 : -1}
            aria-disabled={closing || undefined}
            onPointerDown={(event) => event.stopPropagation()}
            onMouseDown={(event) => event.stopPropagation()}
            onClick={(event) => {
              event.stopPropagation();
              if (!closing) onClose(event, memberTabs.filter((tab) => !closingIds.has(tab.id)));
            }}
          >
            <X size={14} />
          </button>
        </span>
      </div>
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
  useEffect(() => {
    if (!active) return;
    const flow = tabRef.current?.closest<HTMLElement>('[role="tablist"]');
    // Scroll the strip itself, never scrollIntoView: that also scrolls
    // every scrollable ancestor, and with an overflowing strip it pushed
    // the whole centre column sideways and the composer off screen.
    const revealActiveTab = () => {
      const tab = tabRef.current;
      if (!tab || !flow) return;
      const left = tab.offsetLeft - flow.offsetLeft;
      const right = left + tab.offsetWidth;
      if (left < flow.scrollLeft) flow.scrollLeft = left;
      else if (right > flow.scrollLeft + flow.clientWidth) flow.scrollLeft = right - flow.clientWidth;
    };
    revealActiveTab();
    if (!flow || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(revealActiveTab);
    observer.observe(flow);
    return () => observer.disconnect();
  }, [active]);
  return (
    <div
      ref={tabRef}
      className={`${styles.tab} ${active ? styles.tabActive : ""} ${entering ? styles.tabEnter : ""} ${closing ? styles.tabExit : ""}`}
      style={shiftStyle(shiftX)}
      // Read by freezeStripWidths: a tab mid-exit must keep its shrinking
      // width, not be pinned to the width it had when the freeze ran.
      data-tab-closing={closing || undefined}
      onAnimationEnd={(e) => {
        if (e.target !== e.currentTarget) return;
        if (closing) onExited(tab);
        else setEntering(false);
      }}
      data-tip={tab.kind === "file" ? tab.path : tab.kind === "web" ? readableUrl(tab.url) : label}
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
          {routePageIcon(tab.navigationRoute, 13) ?? (tab.kind === "session" ? (
            <MessageCircleIcon ref={iconRef} size={14} />
          ) : tab.kind === "application" ? (
        <ApplicationTabGlyph appId={tab.applicationId} size={14} />
      ) : tab.kind === "file" ? (
            <FileTypeIcon name={tab.path ?? ""} size={14} />
          ) : tab.kind === "web" ? (
            <TabFavicon url={tab.faviconUrl} />
          ) : tab.kind === "builtin" ? (
            tab.page === "files"
              ? <FileText size={14} />
              : tab.page === "review"
              ? <FeatherIcon size={14} />
              : tab.page === "history"
              ? <History size={14} />
              : tab.page === "downloads"
                ? <Download size={14} />
              : tab.page === "office"
                ? <FilePen size={14} />
              : tab.page === "recording"
                ? <CircleDot size={14} />
              : tab.page === "meeting"
                ? <Mic size={14} />
              : tab.page === "browser"
                ? <ChromeIcon size={14} />
                : tab.page === "terminal" || tab.page === "claude"
                  ? <TerminalSquare size={14} />
                  : <Bookmark size={14} />
          ) : (
            <CirclePlus size={14} />
          ))}
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

/** Offscreen template for a pane grip's drag; shares the strip tab styling. */
export function PaneDragLabel({ tab }: { tab: CenterTab }) {
  const { t, text } = useTranslation();
  return <span data-pane-drag-label hidden style={{ display: "none" }} className={styles.tab}>
    <span className={styles.tabTarget}>
      <CompoundMemberIcon tab={tab} animate={false} />
      <span className={styles.tabName}>{labelOf(tab, t, text)}</span>
    </span>
    <span className={styles.tabClose} aria-hidden="true"><X size={12} /></span>
  </span>;
}
