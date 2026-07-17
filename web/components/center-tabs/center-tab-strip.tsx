"use client";

/**
 * CenterTabStrip — the browser-style tab row over the center column.
 *
 * Session tabs are bookmarks over the SINGLETON chat surface: clicking
 * one activates its session through the same path the left sidebar
 * uses (router.push("/s/<id>")); the chat DOM itself never remounts.
 * This component also owns the session↔tab sync:
 *   - any session activation (left sidebar, deep link, chat_ack of a
 *     new chat) upserts + focuses its tab
 *   - /chat with no session focuses the draft new-chat tab
 *   - conversation title changes rename their tab
 *
 * Closing a tab with unsaved edits (dirty=true, set by the file
 * editor) asks for confirmation first and, on discard, drops the
 * surviving fileDrafts buffer so reopening starts clean.
 * ponytail: window.confirm — the strip has no dialog host; swap for
 * ConfirmDialog if one ever lands at this level.
 */
import { useEffect, useRef } from "react";
import { usePathname, useRouter } from "next/navigation";
import { CirclePlus, FileText, Globe, Plus, X } from "lucide-react";

import {
  MessageCircleIcon,
  type AnimatedNavIconHandle,
} from "@/components/animated-icons";
import { useCenterTabs, type CenterTab } from "@/lib/state/center-tabs-store";
import { fileDraftKey, fileDrafts } from "@/lib/state/files-shared";
import { useSessionStore } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import styles from "./center-tabs.module.css";

function isChatRoute(pathname: string) {
  return pathname === "/chat" || pathname.startsWith("/s/");
}

export function CenterTabStrip() {
  const router = useRouter();
  const pathname = usePathname();
  const { t, text } = useTranslation();

  const tabs = useCenterTabs((s) => s.tabs);
  const activeId = useCenterTabs((s) => s.activeId);
  const setActive = useCenterTabs((s) => s.setActive);
  const openSessionTab = useCenterTabs((s) => s.openSessionTab);
  const openDraftSessionTab = useCenterTabs((s) => s.openDraftSessionTab);
  const openNewTabPage = useCenterTabs((s) => s.openNewTabPage);
  const closeTab = useCenterTabs((s) => s.closeTab);
  const renameSessionTab = useCenterTabs((s) => s.renameSessionTab);

  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const conversations = useSessionStore((s) => s.conversations);

  // Session activation → upsert/focus its tab. The draft tab morphs
  // into the real session tab in place when chat_ack assigns an id
  // (and, browser-style, an active draft/new-tab page is "navigated"
  // by a sidebar session click).
  useEffect(() => {
    if (!isChatRoute(pathname)) return;
    if (currentSessionId) {
      const title =
        useSessionStore.getState().conversations[currentSessionId]?.title ?? "";
      openSessionTab(currentSessionId, title);
    } else if (pathname === "/chat") {
      openDraftSessionTab();
    }
  }, [currentSessionId, pathname, openSessionTab, openDraftSessionTab]);

  // Title changes → rename tabs (covers renames + first-message titles).
  // Same pass reaps zombie tabs: a session tab whose conversation was
  // JUST removed from the list (sidebar delete / clear-all) would
  // otherwise linger with a dead navigation target. Reap only ids seen
  // in the previous list and gone now — a merely stale localStorage
  // restore or a not-yet-loaded list must not close tabs.
  const prevConvIds = useRef<Set<string> | null>(null);
  useEffect(() => {
    const ids = new Set(Object.keys(conversations));
    const prev = prevConvIds.current;
    prevConvIds.current = ids;
    for (const tab of useCenterTabs.getState().tabs) {
      if (tab.kind !== "session" || !tab.sessionId) continue;
      if (prev?.has(tab.sessionId) && !ids.has(tab.sessionId)) {
        useCenterTabs.getState().closeTab(tab.id);
        continue;
      }
      const title = conversations[tab.sessionId]?.title;
      if (title && title !== tab.title) renameSessionTab(tab.sessionId, title);
    }
  }, [conversations, renameSessionTab]);

  /** Navigate the live chat to a session tab's conversation — the
   *  exact call path sessions-list uses (router.push on /s/<id>). */
  function activateSession(tab: CenterTab) {
    const sid = useSessionStore.getState().currentSessionId;
    if (tab.sessionId) {
      if (tab.sessionId !== sid || !pathname.startsWith("/s/")) {
        router.push("/s/" + tab.sessionId);
      }
    } else if (pathname !== "/chat") {
      router.push("/chat"); // draft tab → new-chat route (resets in place)
    }
  }

  function onTabClick(tab: CenterTab) {
    setActive(tab.id);
    if (tab.kind === "session") activateSession(tab);
  }

  function onTabClose(e: React.MouseEvent, tab: CenterTab) {
    e.stopPropagation();
    if (tab.dirty) {
      if (!window.confirm(text("Discard unsaved changes?", "放弃未保存的修改？")))
        return;
      // Discard confirmed — drop the surviving draft buffer too, so
      // reopening the file starts from disk, not the "discarded" edit.
      if (tab.kind === "file" && tab.projectId && tab.path)
        fileDrafts.delete(fileDraftKey(tab.projectId, tab.path));
    }
    closeTab(tab.id);
    // Closing the active tab hands focus to a neighbor; if that
    // neighbor is a session tab, bring the chat surface to it.
    const s = useCenterTabs.getState();
    const next = s.tabs.find((x) => x.id === s.activeId);
    if (next && next.kind === "session" && next.id !== tab.id) {
      activateSession(next);
    }
  }

  function labelOf(tab: CenterTab): string {
    if (tab.kind === "ntp") return text("New tab", "新标签页");
    if (tab.kind === "file") return tab.title;
    if (tab.kind === "web") return tab.title || tab.url || "";
    if (!tab.sessionId) return text("New chat", "新会话");
    return tab.title || t("sidebar.untitled");
  }

  return (
    <div className={styles.strip}>
      {tabs.map((tab) => (
        <TabItem
          key={tab.id}
          tab={tab}
          active={tab.id === activeId}
          label={labelOf(tab)}
          closeLabel={text("Close tab", "关闭标签")}
          onClick={() => onTabClick(tab)}
          onClose={(e) => onTabClose(e, tab)}
        />
      ))}
      <button
        type="button"
        className={styles.plusBtn}
        title={text("New tab", "新标签页")}
        onClick={openNewTabPage}
      >
        <Plus size={15} />
      </button>
    </div>
  );
}

/** One strip tab. A component (not map-inline JSX) so each session
 *  tab owns the ref that drives its animated icon from row hover. */
function TabItem({
  tab,
  active,
  label,
  closeLabel,
  onClick,
  onClose,
}: {
  tab: CenterTab;
  active: boolean;
  label: string;
  closeLabel: string;
  onClick: () => void;
  onClose: (e: React.MouseEvent) => void;
}) {
  const iconRef = useRef<AnimatedNavIconHandle>(null);
  return (
    <div
      className={`${styles.tab} ${active ? styles.tabActive : ""}`}
      title={tab.kind === "file" ? tab.path : tab.kind === "web" ? tab.url : label}
      onClick={onClick}
      onMouseEnter={() => iconRef.current?.startAnimation?.()}
      onMouseLeave={() => iconRef.current?.stopAnimation?.()}
      // Middle-click closes (browser convention). preventDefault on
      // mousedown stops the autoscroll cursor; the close itself
      // fires on auxclick.
      onMouseDown={(e) => {
        if (e.button === 1) e.preventDefault();
      }}
      onAuxClick={(e) => {
        if (e.button === 1) {
          e.preventDefault();
          onClose(e);
        }
      }}
    >
      {/* 悬停浮起的浅色 pill，绝对定位铺在标签内容之下（z-index:-1），
          非活动标签悬停时点亮；活动标签下它保持透明。 */}
      <span className={styles.tabPill} aria-hidden="true" />
      <span className={styles.tabIcon} aria-hidden="true">
        {tab.kind === "session" ? (
          <MessageCircleIcon ref={iconRef} size={14} />
        ) : tab.kind === "file" ? (
          <FileText size={13} />
        ) : tab.kind === "web" ? (
          <Globe size={13} />
        ) : (
          <CirclePlus size={13} />
        )}
      </span>
      <span className={styles.tabName}>{label}</span>
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
      <span
        role="button"
        className={styles.tabClose}
        aria-label={closeLabel}
        onClick={onClose}
      >
        <X size={12} />
      </span>
    </div>
  );
}
