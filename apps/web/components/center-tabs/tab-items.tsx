"use client";

/**
 * Strip tab presentation — the two components the strip maps over.
 *
 * `TabItem` is one ordinary top-level tab. `CompoundTabItem` is one top-level
 * tab whose content surface contains a split-view group. Split members are
 * not independently selectable from the strip.
 */
import { useEffect, useRef, useState } from "react";
import { Bookmark, CirclePlus, Download, FileText, History, TerminalSquare, X } from "lucide-react";

import {
  ChromeIcon,
  FeatherIcon,
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
  onDragPointerDown(
    subject: TabDragSubject,
    event: React.PointerEvent<HTMLElement>,
  ): void;
}

function CompoundMemberIcon({ tab, animate }: { tab: CenterTab; animate: boolean }) {
  const iconRef = useRef<AnimatedNavIconHandle>(null);
  const [brokenFavicon, setBrokenFavicon] = useState("");
  useEffect(() => {
    if (animate) iconRef.current?.startAnimation?.();
    else iconRef.current?.stopAnimation?.();
  }, [animate]);
  return (
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
          <ChromeIcon size={13} />
        )
      ) : tab.kind === "builtin" ? (
        tab.page === "files"
          ? <FileText size={13} />
          : tab.page === "review"
          ? <FeatherIcon size={13} />
          : tab.page === "history"
          ? <History size={13} />
          : tab.page === "downloads"
            ? <Download size={13} />
          : tab.page === "browser"
            ? <ChromeIcon size={13} />
            : tab.page === "terminal" || tab.page === "claude"
              ? <TerminalSquare size={13} />
              : <Bookmark size={13} />
      ) : (
        <CirclePlus size={13} />
      )}
    </span>
  );
}

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
  onDragPointerDown,
}: CompoundTabItemProps) {
  const { t, text } = useTranslation();
  const [hovered, setHovered] = useState(false);
  const active = group.memberIds.includes(activeId ?? "");
  const memberTabs = group.memberIds.flatMap((tabId) => {
    const tab = tabs.find((candidate) => candidate.id === tabId);
    return tab ? [tab] : [];
  });
  const canonicalTab = memberTabs.find((tab) => tab.kind === "session")
    ?? memberTabs.find((tab) => tab.id === group.focusedId)
    ?? memberTabs[0];
  if (!canonicalTab) return null;
  const combinedLabel = memberTabs.map((tab) => labelOf(tab, t, text)).join(" · ");
  const closingTabs = memberTabs.filter((tab) => closingIds.has(tab.id));
  const closing = closingTabs.length === memberTabs.length;
  const memberClosing = closingTabs.length > 0 && !closing;
  const tabStop = group.memberIds.includes(focusedTabId ?? "");
  return (
    <div
      className={`${styles.compoundTab} ${active ? styles.compoundTabActive : ""} ${closing ? styles.tabExit : ""} ${memberClosing ? styles.compoundMemberClosing : ""}`}
      data-member-count={group.memberIds.length}
      data-tab-closing={closing || undefined}
      style={shiftStyle(shiftX)}
      role="presentation"
      title={combinedLabel}
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
        {memberTabs.map((tab) => {
          const memberClosing = !closing && closingIds.has(tab.id);
          const memberLabel = labelOf(tab, t, text);
          const closeLabel = `${text("Close", "关闭")} ${memberLabel}`;
          return (
            <span
              key={tab.id}
              className={`${styles.compoundMember} ${memberClosing ? styles.compoundMemberExit : ""}`}
              onAnimationEnd={(event) => {
                if (event.target === event.currentTarget && memberClosing) onExited(tab);
              }}
            >
              <CompoundMemberIcon tab={tab} animate={hovered} />
              <span className={styles.compoundMemberName} aria-hidden="true">
                {memberLabel}
              </span>
              {tab.dirty ? (
                <span className={styles.tabDirtyDot} aria-hidden="true">
                  <span style={{ width: 8, height: 8, borderRadius: "50%", background: "currentColor" }} />
                </span>
              ) : null}
              <button
                type="button"
                className={`${styles.tabClose} ${styles.compoundMemberClose}`}
                aria-label={closeLabel}
                title={closeLabel}
                tabIndex={active ? 0 : -1}
                aria-disabled={closingIds.has(tab.id) || undefined}
                onPointerDown={(event) => event.stopPropagation()}
                onMouseDown={(event) => event.stopPropagation()}
                onClick={(event) => {
                  event.stopPropagation();
                  if (!closingIds.has(tab.id)) onClose(event, [tab]);
                }}
              >
                <X size={14} />
              </button>
            </span>
          );
        })}
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
              <ChromeIcon size={13} />
            )
          ) : tab.kind === "builtin" ? (
            tab.page === "files"
              ? <FileText size={13} />
              : tab.page === "review"
              ? <FeatherIcon size={13} />
              : tab.page === "history"
              ? <History size={13} />
              : tab.page === "downloads"
                ? <Download size={13} />
              : tab.page === "browser"
                ? <ChromeIcon size={13} />
                : tab.page === "terminal" || tab.page === "claude"
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
