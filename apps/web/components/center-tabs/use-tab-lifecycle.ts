"use client";

/**
 * Session ↔ tab synchronisation and the tab open/close animation lifecycle.
 *
 * Session tabs are bookmarks over the singleton chat surface, so every
 * activation path (left sidebar, deep link, chat_ack of a new chat) has to
 * converge here: this hook upserts + focuses the matching tab, renames tabs
 * when their conversation title changes, reaps tabs whose conversation was
 * just deleted, and owns the enter/exit animation bookkeeping
 * (`enteringIds` / `closingIds`) that the strip renders from.
 */
import { useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";

import { useCenterTabs, type CenterTab } from "@/lib/state/center-tabs-store";
import { findCenterTabGroup } from "@/lib/state/center-tab-groups";
import { useSessionStore } from "@/lib/session-store";
import { newSession } from "@/lib/runtime-bridge/conversations";
import { discardFileDraft } from "@/lib/state/files-shared";
import { deleteAttachments } from "@/components/chat/composer/attach/attach-idb";
import {
  draftChannelChoiceHost,
  dropDraftChannelChoice,
} from "@/lib/runtime-bridge/draft-channel-choice";
import { pushPath } from "@/lib/shallow-nav";
import { useTranslation } from "@/lib/i18n";

export function isChatRoute(pathname: string) {
  return pathname === "/chat" || pathname.startsWith("/s/");
}

export interface TabLifecycleOptions {
  /** Cancel any in-flight drag before a close mutates the strip. */
  cancelDrag(): void;
  /** Active tab id — a click on the already-active session tab bumps the
   *  activation request so it can recover from another route. */
  activeId: string | null;
  setFocusedTabId(tabId: string | null): void;
  /** Pin the surviving tabs to their current widths (Chrome's close-with-
   *  the-mouse freeze). Only the mouse close path calls this. */
  freezeWidthsForMouseClose(closeTarget?: EventTarget | null): void;
  /** Drop any width freeze so the strip reflows normally again. */
  releaseFrozenWidths(): void;
}

export function useTabLifecycle({
  cancelDrag,
  activeId,
  setFocusedTabId,
  freezeWidthsForMouseClose,
  releaseFrozenWidths,
}: TabLifecycleOptions) {
  const router = useRouter();
  const pathname = usePathname();
  const { text } = useTranslation();

  const tabs = useCenterTabs((s) => s.tabs);
  const setActive = useCenterTabs((s) => s.setActive);
  const openSessionTab = useCenterTabs((s) => s.openSessionTab);
  const openDraftSessionTab = useCenterTabs((s) => s.openDraftSessionTab);
  const openNewTabPage = useCenterTabs((s) => s.openNewTabPage);
  const closeTab = useCenterTabs((s) => s.closeTab);
  const renameSessionTab = useCenterTabs((s) => s.renameSessionTab);

  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const conversations = useSessionStore((s) => s.conversations);
  const [sessionActivationRequest, setSessionActivationRequest] = useState(0);
  // Tab id activated by the current pointerdown, consumed by the click
  // that follows it (see onTabPointerDown / onTabClickFromPointer).
  const activatedOnPressRef = useRef<string | null>(null);

  // Session activation → upsert/focus its tab. The draft tab morphs
  // into the real session tab in place when chat_ack assigns an id
  // (and, browser-style, an active draft/new-tab page is "navigated"
  // by a sidebar session click).
  useEffect(() => {
    if (!isChatRoute(pathname)) return;
    const centerTabs = useCenterTabs.getState();
    const activeTab = centerTabs.tabs.find((t) => t.id === centerTabs.activeId);
    // newSession clears the session store before router.push('/chat'). During
    // that render pathname can still be /s/<old>; the claimed draft is the
    // intended destination, so do not resurrect the stale route's session.
    if (
      currentSessionId === null &&
      activeTab?.draft &&
      pathname.startsWith("/s/")
    ) return;
    // 会话 id 以路由为准：关 tab 后 activateSession 推新路由时，pathname
    // 和 currentSessionId 分两次渲染更新 —— 若用 currentSessionId，中间那
    // 帧会把刚关掉的会话 tab 重新插回来（"关一个弹回一个"）。路由是唯一
    // 不滞后的真值。
    if (pathname.startsWith("/s/")) {
      const sid = decodeURIComponent(pathname.slice("/s/".length));
      const title = useSessionStore.getState().conversations[sid]?.title ?? "";
      openSessionTab(sid, title);
    } else if (currentSessionId) {
      // /chat 且 chat_ack 已分配 id → 草稿 tab 原地转正。
      const title =
        useSessionStore.getState().conversations[currentSessionId]?.title ?? "";
      openSessionTab(currentSessionId, title);
    } else if (activeTab?.kind === "session" && activeTab.draft && activeTab.sessionId) {
      useSessionStore.getState().setCurrentDraft(activeTab.sessionId);
    } else if (centerTabs.tabs.length > 0) {
      // 桌面端每次启动都落在 /chat：已有恢复出来的标签时，这只是默认
      // 启动 URL，不是用户要新建草稿——否则每次重启都会多出一枚标签。
    } else {
      const draftId = openDraftSessionTab();
      useSessionStore.getState().setCurrentDraft(draftId);
    }
  }, [currentSessionId, pathname, openSessionTab, openDraftSessionTab]);

  // Title changes → rename tabs (covers renames + first-message titles).
  // Same pass reaps zombie tabs: a session tab whose conversation was
  // JUST removed from the list (sidebar delete / clear-all) would
  // otherwise linger with a dead navigation target. Reap only ids seen
  // in the previous list and gone now — a merely stale localStorage
  // restore or a not-yet-loaded list must not close tabs.
  const prevConvIds = useRef<Set<string> | null>(null);
  // 退场动画按 id 维持，标题/dirty 等 immutable 更新不能取消关闭；同时
  // 保存开始关闭时的实例，标题/dirty 等 immutable 更新不能取消关闭。
  const closingInstances = useRef<Map<string, CenterTab>>(new Map());
  const [closingIds, setClosingIds] = useState<Set<string>>(new Set());
  useEffect(() => {
    const invalid: string[] = [];
    for (const id of closingInstances.current.keys()) {
      const current = tabs.find((tab) => tab.id === id);
      if (!current) {
        invalid.push(id);
      }
    }
    if (invalid.length === 0) return;
    for (const id of invalid) closingInstances.current.delete(id);
    setClosingIds((prev) => {
      const next = new Set(prev);
      for (const id of invalid) next.delete(id);
      return next;
    });
  }, [tabs]);
  // 入场动画：上一次提交后的 tab id 集合。本次渲染里不在集合中的 = 新增，
  // 挂 .tabEnter 播"从 0 长宽、挤开邻居"的动画；首次渲染（null）不播。
  // tab 总数没变多 = NTP 原地变身（id 换了但位置复用，如空 tab 上点侧栏
  // 会话），不是真追加 —— 也不播，避免原地弹一下。
  const prevTabIds = useRef<Set<string> | null>(null);
  const enteringIds =
    prevTabIds.current === null || tabs.length <= prevTabIds.current.size
      ? new Set<string>()
      : new Set(tabs.filter((t) => !prevTabIds.current!.has(t.id)).map((t) => t.id));
  useEffect(() => {
    prevTabIds.current = new Set(tabs.map((t) => t.id));
  }, [tabs]);
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
    if (tab.draft && tab.sessionId) {
      newSession(tab.sessionId);
      return;
    }
    const sid = useSessionStore.getState().currentSessionId;
    if (tab.sessionId) {
      if (tab.sessionId !== sid || !pathname.startsWith("/s/")) {
        pushPath("/s/" + tab.sessionId);
      }
    } else if (pathname !== "/chat") {
      router.push("/chat"); // draft tab → new-chat route (resets in place)
    }
  }

  // Active center-tab focus is the single session-navigation trigger. Store
  // imports and close fallback converge on activeId; clicking the already
  // active session increments the request so it can recover from another route.
  const activationMounted = useRef(false);
  useEffect(() => {
    if (!activationMounted.current) {
      activationMounted.current = true;
      // Initial mount on a deep link (/skills/x, /settings/…, /s/<id>):
      // the persisted active session tab must NOT hijack the route the
      // user actually opened — /s/<id> deep links included; the
      // pathname-watcher effect adopts that session into a tab instead.
      // Later activeId changes are real user tab switches and navigate
      // as before.
      if (!isChatRoute(pathname) || pathname.startsWith("/s/")) return;
    }
    const tab = useCenterTabs.getState().tabs.find(
      (candidate) => candidate.id === activeId,
    );
    if (tab?.kind === "session") activateSession(tab);
    // Route changes are results of activation, not new activation requests.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId, sessionActivationRequest]);

  useEffect(() => {
    setFocusedTabId(activeId);
    // setFocusedTabId is a stable setState setter.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId]);

  /** Click handler for strip tabs. pointerdown already activated the tab
   *  (Chrome activates on press), so the click that completes that same
   *  press is a no-op; a click on the already-active tab still reloads. */
  function onTabClickFromPointer(tab: CenterTab) {
    if (activatedOnPressRef.current === tab.id) {
      activatedOnPressRef.current = null;
      return;
    }
    activatedOnPressRef.current = null;
    onTabClick(tab);
  }

  function onTabClick(tab: CenterTab) {
    const reactivateCurrentSession =
      tab.kind === "session" && tab.id === activeId;
    setFocusedTabId(tab.id);
    setActive(tab.id);
    if (reactivateCurrentSession) {
      setSessionActivationRequest((request) => request + 1);
    }
    if (tab.kind !== "session" && !isChatRoute(pathname)) router.push("/chat");
  }

  function onOpenNewTab() {
    // A new tab relayouts the whole row anyway, so any close-run freeze
    // ends here rather than fighting the enter animation.
    releaseFrozenWidths();
    openNewTabPage();
    if (!isChatRoute(pathname)) router.push("/chat");
  }

  /** Chrome freezes the surviving tab widths only when the close came from
   *  the MOUSE — a run of × clicks must keep the next × under the cursor.
   *
   * A keyboard activation of the × button also arrives as a click, but with
   * `detail === 0` (no physical click count), so it reflows immediately.
   * Programmatic closes — the context menu (which passes a stub carrying
   * only stopPropagation, hence no nativeEvent), a deleted session, a tab
   * dragged out — never reach a MouseEvent here at all. */
  function isMouseDrivenClose(e: React.SyntheticEvent) {
    const native = e.nativeEvent as Event | undefined;
    return (
      typeof MouseEvent !== "undefined"
      && native instanceof MouseEvent
      && native.detail > 0
    );
  }

  function onTabsClose(e: React.SyntheticEvent, tabsToClose: CenterTab[]) {
    e.stopPropagation();
    cancelDrag();
    if (tabsToClose.some((tab) => tab.dirty)) {
      if (!window.confirm(text("Discard unsaved changes?", "放弃未保存的修改？")))
        return;
      // Discard confirmed — drop the surviving draft buffer too, so
      // reopening the file starts from disk, not the "discarded" edit.
      for (const tab of tabsToClose) {
        if (tab.kind === "file" && tab.projectId && tab.path)
          void discardFileDraft(tab.projectId, tab.path);
      }
    }
    // Pin the survivors' widths for a mouse close (Chrome), so the next
    // tab's × stays under the cursor; every other close path reflows now.
    // Runs AFTER the discard prompt: a cancelled close must not freeze.
    // The strip marks the closing row entry BEFORE pinning — React only
    // stamps data-tab-closing on the next render, which is after
    // freezeStripWidths has walked the row, so without the early mark the
    // closing tab gets pinned too and its exit shrink fights the inline
    // width.
    if (isMouseDrivenClose(e)) freezeWidthsForMouseClose(e.target);
    else releaseFrozenWidths();
    // 先播退场动画（.tabExit 收缩到 0），animationend 再 finishClose 真正
    // 移除 —— 和新建 tab 的挤压动画成镜像。
    for (const tab of tabsToClose) closingInstances.current.set(tab.id, tab);
    setClosingIds((prev) => {
      const next = new Set(prev);
      for (const tab of tabsToClose) next.add(tab.id);
      return next;
    });
  }

  const onTabsCloseRef = useRef(onTabsClose);
  onTabsCloseRef.current = onTabsClose;
  useEffect(() => {
    const onDesktopClose = () => {
      const state = useCenterTabs.getState();
      if (!state.activeId) return;
      const group = findCenterTabGroup(state.groups, state.activeId);
      const ids = group?.memberIds ?? [state.activeId];
      const tabsToClose = ids.flatMap((id) => {
        const tab = state.tabs.find((candidate) => candidate.id === id);
        return tab ? [tab] : [];
      });
      if (tabsToClose.length > 0) {
        onTabsCloseRef.current(
          { stopPropagation: () => {} } as React.SyntheticEvent,
          tabsToClose,
        );
      }
    };
    window.addEventListener("op-desktop-close-tab", onDesktopClose);
    return () => window.removeEventListener("op-desktop-close-tab", onDesktopClose);
  }, []);

  function onTabClose(e: React.SyntheticEvent, tab: CenterTab) {
    onTabsClose(e, [tab]);
  }

  /** 退场动画播完后的真正关闭：移出 store + 焦点交给邻居。 */
  function finishClose(tab: CenterTab) {
    const closingInstance = closingInstances.current.get(tab.id);
    closingInstances.current.delete(tab.id);
    setClosingIds((prev) => {
      const next = new Set(prev);
      next.delete(tab.id);
      return next;
    });
    if (!closingInstance) return;
    const currentTab = useCenterTabs.getState().tabs.find((x) => x.id === tab.id);
    if (!currentTab) return;
    closeTab(tab.id);
    if (tab.draft && tab.sessionId) {
      useSessionStore.getState().dropChatDraft(tab.sessionId);
      dropDraftChannelChoice(draftChannelChoiceHost, tab.sessionId);
      void deleteAttachments(tab.sessionId);
    }
  }

  return {
    tabs,
    enteringIds,
    closingIds,
    activatedOnPressRef,
    onTabClick,
    onTabClickFromPointer,
    onOpenNewTab,
    onTabClose,
    onTabsClose,
    finishClose,
  };
}
