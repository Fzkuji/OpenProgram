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
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { CirclePlus, FileText, Globe, GripVertical, Plus, X } from "lucide-react";

import {
  MessageCircleIcon,
  type AnimatedNavIconHandle,
} from "@/components/animated-icons";
import {
  useCenterTabs,
  type CenterTab,
} from "@/lib/state/center-tabs-store";
import {
  centerTabStripEntries,
  findCenterTabGroup,
  type CenterTabGroup,
} from "@/lib/state/center-tab-groups";
import {
  dragCoordinator,
  resolveTabDropIntent,
  type TabDragSubject,
  type TabDropIntent,
} from "@/lib/tab-drag-coordinator";
import { deleteAttachments } from "@/components/chat/composer/attach/attach-idb";
import {
  dropDraftChannelChoice,
  type DraftChannelChoiceHost,
} from "@/lib/runtime-bridge/draft-channel-choice";
import { fileDraftKey, fileDrafts } from "@/lib/state/files-shared";
import { useSessionStore } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import styles from "./center-tabs.module.css";

const TAB_DRAG_MIME = "application/x-openprogram-tab";
let removePreparedReleaseListener: (() => void) | null = null;

function removeReleaseListener() {
  removePreparedReleaseListener?.();
  removePreparedReleaseListener = null;
}

function isChatRoute(pathname: string) {
  return pathname === "/chat" || pathname.startsWith("/s/");
}

function labelOf(
  tab: CenterTab,
  t: ReturnType<typeof useTranslation>["t"],
  text: ReturnType<typeof useTranslation>["text"],
): string {
  if (tab.kind === "ntp") return text("New tab", "新标签页");
  if (tab.kind === "file") return tab.title;
  if (tab.kind === "web") return tab.title || tab.url || "";
  if (tab.draft) return text("New chat", "新会话");
  return tab.title || t("sidebar.untitled");
}

export function CenterTabStrip() {
  const router = useRouter();
  const pathname = usePathname();
  const { t, text } = useTranslation();

  const tabs = useCenterTabs((s) => s.tabs);
  const groups = useCenterTabs((s) => s.groups);
  const activeId = useCenterTabs((s) => s.activeId);
  const setActive = useCenterTabs((s) => s.setActive);
  const openSessionTab = useCenterTabs((s) => s.openSessionTab);
  const openDraftSessionTab = useCenterTabs((s) => s.openDraftSessionTab);
  const openNewTabPage = useCenterTabs((s) => s.openNewTabPage);
  const closeTab = useCenterTabs((s) => s.closeTab);
  const renameSessionTab = useCenterTabs((s) => s.renameSessionTab);
  const moveTab = useCenterTabs((s) => s.moveTab);
  const moveGroup = useCenterTabs((s) => s.moveGroup);
  const moveGroupMember = useCenterTabs((s) => s.moveGroupMember);
  const groupTab = useCenterTabs((s) => s.groupTab);
  const ungroupTab = useCenterTabs((s) => s.ungroupTab);

  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const conversations = useSessionStore((s) => s.conversations);
  const [sessionActivationRequest, setSessionActivationRequest] = useState(0);
  const [draggedIds, setDraggedIds] = useState<ReadonlySet<string>>(new Set());
  const [dropMarker, setDropMarker] = useState<TabDropIntent | null>(null);
  const [dragAnnouncement, setDragAnnouncement] = useState("");
  const stripRef = useRef<HTMLDivElement>(null);
  const tabsFlowRef = useRef<HTMLDivElement>(null);
  const plusRef = useRef<HTMLButtonElement>(null);

  // In desktop mode the + follows short tab lists, but reaches the fixed
  // 49px right rail once the tab flow fills the row. Expose that geometric
  // state so CSS can move only the saturated separator onto the rail edge.
  useLayoutEffect(() => {
    const strip = stripRef.current;
    const flow = tabsFlowRef.current;
    const plus = plusRef.current;
    if (!strip || !flow || !plus) return;
    const updateAlignment = () => {
      const paddingRight = Number.parseFloat(getComputedStyle(strip).paddingRight) || 0;
      const contentRight = strip.getBoundingClientRect().right - paddingRight;
      const railAligned = Math.abs(contentRight - plus.getBoundingClientRect().right) < 1;
      strip.toggleAttribute("data-plus-rail-aligned", railAligned);
    };
    updateAlignment();
    const observer = new ResizeObserver(updateAlignment);
    observer.observe(strip);
    observer.observe(flow);
    return () => observer.disconnect();
  }, []);

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
      (window as unknown as { newSession?: (draftId?: string) => void })
        .newSession?.(tab.sessionId);
      return;
    }
    const sid = useSessionStore.getState().currentSessionId;
    if (tab.sessionId) {
      if (tab.sessionId !== sid || !pathname.startsWith("/s/")) {
        router.push("/s/" + tab.sessionId);
      }
    } else if (pathname !== "/chat") {
      router.push("/chat"); // draft tab → new-chat route (resets in place)
    }
  }

  // Active center-tab focus is the single session-navigation trigger. Store
  // imports and close fallback converge on activeId; clicking the already
  // active session increments the request so it can recover from another route.
  useEffect(() => {
    const tab = useCenterTabs.getState().tabs.find(
      (candidate) => candidate.id === activeId,
    );
    if (tab?.kind === "session") activateSession(tab);
    // Route changes are results of activation, not new activation requests.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId, sessionActivationRequest]);

  function onTabClick(tab: CenterTab) {
    const reactivateCurrentSession =
      tab.kind === "session" && tab.id === activeId;
    setActive(tab.id);
    if (reactivateCurrentSession) {
      setSessionActivationRequest((request) => request + 1);
    }
    if (tab.kind !== "session" && !isChatRoute(pathname)) router.push("/chat");
  }

  function onOpenNewTab() {
    openNewTabPage();
    if (!isChatRoute(pathname)) router.push("/chat");
  }

  function onTabClose(e: React.SyntheticEvent, tab: CenterTab) {
    e.stopPropagation();
    cancelDrag();
    if (tab.dirty) {
      if (!window.confirm(text("Discard unsaved changes?", "放弃未保存的修改？")))
        return;
      // Discard confirmed — drop the surviving draft buffer too, so
      // reopening the file starts from disk, not the "discarded" edit.
      if (tab.kind === "file" && tab.projectId && tab.path)
        fileDrafts.delete(fileDraftKey(tab.projectId, tab.path));
    }
    // 先播退场动画（.tabExit 收缩到 0），animationend 再 finishClose 真正
    // 移除 —— 和新建 tab 的挤压动画成镜像。
    closingInstances.current.set(tab.id, tab);
    setClosingIds((prev) => new Set(prev).add(tab.id));
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
      dropDraftChannelChoice(
        window as unknown as DraftChannelChoiceHost,
        tab.sessionId,
      );
      void deleteAttachments(tab.sessionId);
    }
  }

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
    items[nextIndex].focus();
    items[nextIndex].click();
  }

  function onTabListWheel(e: React.WheelEvent<HTMLDivElement>) {
    if (e.currentTarget.scrollWidth <= e.currentTarget.clientWidth) return;
    if (Math.abs(e.deltaX) >= Math.abs(e.deltaY) || e.deltaY === 0) return;
    e.currentTarget.scrollLeft += e.deltaY;
    e.preventDefault();
  }

  const stripEntries = centerTabStripEntries({
    tabIds: tabs.map((tab) => tab.id),
    groups,
  });

  function clearDragState() {
    removeReleaseListener();
    setDraggedIds(new Set());
    setDropMarker(null);
  }

  function cancelDrag(announce = false) {
    dragCoordinator.cancel();
    clearDragState();
    if (announce) setDragAnnouncement(text("Tab drag cancelled", "标签拖动已取消"));
  }

  function onPrepareDrag(subject: TabDragSubject) {
    removeReleaseListener();
    dragCoordinator.cancel();
    const snapshot: TabDragSubject = subject.kind === "tab"
      ? { kind: "tab", tabIds: [subject.tabIds[0]] }
      : subject.kind === "segment"
        ? {
          ...subject,
          tabIds: [subject.tabIds[0]],
          sourceGroup: {
            ...subject.sourceGroup,
            memberIds: [...subject.sourceGroup.memberIds],
            visibleIds: [...subject.sourceGroup.visibleIds],
          },
        }
        : {
            ...subject,
            tabIds: [...subject.tabIds],
            sourceGroup: {
              ...subject.sourceGroup,
              memberIds: [...subject.sourceGroup.memberIds],
              visibleIds: [...subject.sourceGroup.visibleIds],
            },
          };
    dragCoordinator.prepare({
      subject: snapshot,
      started: false,
      cancelled: false,
      committed: false,
    });
    const cancelUnstarted = () => {
      const prepared = dragCoordinator.current();
      if (prepared && !prepared.started) dragCoordinator.cancel();
      removeReleaseListener();
    };
    window.addEventListener("pointerup", cancelUnstarted, { once: true });
    removePreparedReleaseListener = () =>
      window.removeEventListener("pointerup", cancelUnstarted);
  }

  function onDragStart(e: React.DragEvent<HTMLElement>) {
    const started = dragCoordinator.start();
    removeReleaseListener();
    const prepared = dragCoordinator.current();
    if (!started || !prepared) {
      e.preventDefault();
      cancelDrag(true);
      return;
    }
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData(TAB_DRAG_MIME, JSON.stringify(prepared));
    e.dataTransfer.setData("text/plain", prepared.subject.tabIds.join("\n"));
    setDraggedIds(new Set(prepared.subject.tabIds));
    setDragAnnouncement(text("Dragging tab", "正在拖动标签"));
  }

  function onDragEnd() {
    cancelDrag();
  }

  function targetBeforeId(targetTabId: string, after: boolean): string | null {
    const state = useCenterTabs.getState();
    const targetGroup = findCenterTabGroup(state.groups, targetTabId);
    if (!after) return targetGroup?.memberIds[0] ?? targetTabId;
    const lastTargetId = targetGroup?.memberIds.at(-1) ?? targetTabId;
    const targetIndex = state.tabs.findIndex((tab) => tab.id === lastTargetId);
    return state.tabs[targetIndex + 1]?.id ?? null;
  }

  function applyDrop(prepared: NonNullable<ReturnType<typeof dragCoordinator.current>>, intent: TabDropIntent) {
    const subject = prepared.subject;
    if (intent.mode === "merge") {
      if (subject.kind === "group") return false;
      if (subject.kind === "segment" && intent.groupId === subject.sourceGroup.id) {
        const currentGroup = useCenterTabs.getState().groups.find(
          (group) => group.id === subject.sourceGroup.id,
        );
        if (!currentGroup) return false;
        let toIndex = intent.memberIndex ?? currentGroup.memberIds.length;
        const sourceIndex = currentGroup.memberIds.indexOf(subject.tabIds[0]);
        if (sourceIndex >= 0 && sourceIndex < toIndex) toIndex -= 1;
        moveGroupMember(subject.sourceGroup.id, subject.tabIds[0], toIndex);
        return true;
      }
      return groupTab(
        subject.tabIds[0],
        intent.targetTabId,
        intent.memberIndex ?? 1,
        intent.groupId,
      );
    }

    const beforeId = targetBeforeId(intent.targetTabId, intent.mode === "after");
    if (subject.kind === "group") {
      if (subject.tabIds.includes(intent.targetTabId)) return true;
      moveGroup(subject.sourceGroup.id, beforeId);
      return true;
    }
    if (subject.kind === "segment") {
      const targetGroup = findCenterTabGroup(
        useCenterTabs.getState().groups,
        intent.targetTabId,
      );
      if (targetGroup?.id === subject.sourceGroup.id) {
        const targetIndex = targetGroup.memberIds.indexOf(intent.targetTabId);
        let toIndex = targetIndex + (intent.mode === "after" ? 1 : 0);
        const sourceIndex = targetGroup.memberIds.indexOf(subject.tabIds[0]);
        if (sourceIndex >= 0 && sourceIndex < toIndex) toIndex -= 1;
        moveGroupMember(subject.sourceGroup.id, subject.tabIds[0], toIndex);
      } else {
        ungroupTab(subject.tabIds[0]);
        moveTab(subject.tabIds[0], beforeId);
      }
      return true;
    }
    moveTab(subject.tabIds[0], beforeId);
    return true;
  }

  function onDragOver(
    e: React.DragEvent<HTMLElement>,
    target: { tabId: string; groupId?: string; memberIndex?: number },
  ) {
    const prepared = dragCoordinator.current();
    if (!prepared?.started) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    setDropMarker(resolveTabDropIntent(e.currentTarget.getBoundingClientRect(), e.clientX, target));
  }

  function onDragLeave(e: React.DragEvent<HTMLElement>) {
    if (e.relatedTarget instanceof Node && e.currentTarget.contains(e.relatedTarget)) return;
    setDropMarker(null);
  }

  function onDrop(
    e: React.DragEvent<HTMLElement>,
    target: { tabId: string; groupId?: string; memberIndex?: number },
  ) {
    e.preventDefault();
    const prepared = dragCoordinator.current();
    if (!prepared?.started) {
      cancelDrag(true);
      return;
    }
    const intent = resolveTabDropIntent(
      e.currentTarget.getBoundingClientRect(),
      e.clientX,
      target,
    );
    if (applyDrop(prepared, intent)) {
      dragCoordinator.commit();
      setDragAnnouncement(text("Tab moved", "标签已移动"));
      clearDragState();
    } else {
      cancelDrag(true);
    }
  }

  function moveGroupByKeyboard(groupId: string, direction: -1 | 1) {
    const index = stripEntries.findIndex(
      (entry) => entry.kind === "group" && entry.group.id === groupId,
    );
    const target = stripEntries[index + direction];
    if (!target) return;
    const targetTabId = target.kind === "group" ? target.group.memberIds[0] : target.tabId;
    const beforeId = direction < 0 ? targetTabId : targetBeforeId(targetTabId, true);
    moveGroup(groupId, beforeId);
  }

  useEffect(() => {
    const onEscape = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      dragCoordinator.cancel();
      removeReleaseListener();
      setDraggedIds(new Set());
      setDropMarker(null);
      setDragAnnouncement(text("Tab drag cancelled", "标签拖动已取消"));
    };
    window.addEventListener("keydown", onEscape);
    return () => {
      window.removeEventListener("keydown", onEscape);
      dragCoordinator.cancel();
      removeReleaseListener();
    };
  }, [text]);

  return (
    <div ref={stripRef} className={styles.strip}>
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
                enteringIds={enteringIds}
                closingIds={closingIds}
                onActivate={onTabClick}
                onClose={onTabClose}
                onExited={finishClose}
                draggedIds={draggedIds}
                dropMarker={dropMarker}
                onPrepareDrag={onPrepareDrag}
                onDragStart={onDragStart}
                onDragEnd={onDragEnd}
                onDragOver={onDragOver}
                onDragLeave={onDragLeave}
                onDrop={onDrop}
                onMoveGroup={moveGroupByKeyboard}
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
              enter={enteringIds.has(tab.id)}
              closing={closingIds.has(tab.id)}
              label={labelOf(tab, t, text)}
              closeLabel={text("Close tab", "关闭标签")}
              onActivate={onTabClick}
              onClose={onTabClose}
              onExited={finishClose}
              dragSubject={{ kind: "tab", tabIds: [tab.id] }}
              dragSource={draggedIds.has(tab.id)}
              dropIntent={dropMarker?.targetTabId === tab.id ? dropMarker.mode : undefined}
              dropTarget={{ tabId: tab.id }}
              onPrepareDrag={onPrepareDrag}
              onDragStart={onDragStart}
              onDragEnd={onDragEnd}
              onDragOver={onDragOver}
              onDragLeave={onDragLeave}
              onDrop={onDrop}
            />
          );
        })}
      </div>
      <button
        ref={plusRef}
        type="button"
        className={styles.plusBtn}
        title={text("New tab", "新标签页")}
        aria-label={text("New tab", "新标签页")}
        onClick={onOpenNewTab}
      >
        <Plus size={15} />
      </button>
      <span className={styles.dragAnnouncement} role="status" aria-live="polite">
        {dragAnnouncement}
      </span>
    </div>
  );
}

interface CompoundTabItemProps {
  group: CenterTabGroup;
  tabs: CenterTab[];
  activeId: string | null;
  enteringIds: ReadonlySet<string>;
  closingIds: ReadonlySet<string>;
  onActivate(tab: CenterTab): void;
  onClose(event: React.SyntheticEvent, tab: CenterTab): void;
  onExited(tab: CenterTab): void;
  draggedIds: ReadonlySet<string>;
  dropMarker: TabDropIntent | null;
  onPrepareDrag(subject: TabDragSubject): void;
  onDragStart(event: React.DragEvent<HTMLElement>): void;
  onDragEnd(): void;
  onDragOver(
    event: React.DragEvent<HTMLElement>,
    target: { tabId: string; groupId?: string; memberIndex?: number },
  ): void;
  onDragLeave(event: React.DragEvent<HTMLElement>): void;
  onDrop(
    event: React.DragEvent<HTMLElement>,
    target: { tabId: string; groupId?: string; memberIndex?: number },
  ): void;
  onMoveGroup(groupId: string, direction: -1 | 1): void;
}

function CompoundTabItem({
  group,
  tabs,
  activeId,
  enteringIds,
  closingIds,
  onActivate,
  onClose,
  onExited,
  draggedIds,
  dropMarker,
  onPrepareDrag,
  onDragStart,
  onDragEnd,
  onDragOver,
  onDragLeave,
  onDrop,
  onMoveGroup,
}: CompoundTabItemProps) {
  const { t, text } = useTranslation();
  const active = group.memberIds.includes(activeId ?? "");
  const closingCount = group.memberIds.filter((tabId) =>
    closingIds.has(tabId),
  ).length;
  const remainingCount = group.memberIds.length - closingCount;
  const groupDragged = group.memberIds.every((tabId) => draggedIds.has(tabId));
  const groupDropIntent = dropMarker && group.memberIds.includes(dropMarker.targetTabId)
      && dropMarker.mode !== "merge"
    ? dropMarker.mode
    : undefined;
  return (
    <div
      className={`${styles.compoundTab} ${active ? styles.compoundTabActive : ""}`}
      data-member-count={group.memberIds.length}
      data-closing-count={closingCount || undefined}
      data-remaining-count={remainingCount}
      data-drag-source={groupDragged || undefined}
      data-drop-intent={groupDropIntent}
      role="presentation"
    >
      <button
        type="button"
        className={styles.groupDragHandle}
        draggable
        aria-label={text("Move tab group", "移动标签组")}
        title={text("Move tab group", "移动标签组")}
        onPointerDown={(event) => {
          if (event.button !== 0) return;
          event.stopPropagation();
          onPrepareDrag({
            kind: "group",
            tabIds: [...group.memberIds],
            sourceGroup: group,
          });
        }}
        onDragStart={onDragStart}
        onDragEnd={onDragEnd}
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
            enter={enteringIds.has(tab.id)}
            closing={closingIds.has(tab.id)}
            label={labelOf(tab, t, text)}
            closeLabel={text("Close tab", "关闭标签")}
            segment
            onActivate={onActivate}
            onClose={onClose}
            onExited={onExited}
            dragSubject={{
              kind: "segment",
              tabIds: [tab.id],
              sourceGroup: group,
              memberIndex,
            }}
            dragSource={!groupDragged && draggedIds.has(tab.id)}
            dropIntent={dropMarker?.targetTabId === tab.id && dropMarker.mode === "merge"
              ? "merge"
              : undefined}
            dropTarget={{ tabId: tab.id, groupId: group.id, memberIndex: memberIndex + 1 }}
            onPrepareDrag={onPrepareDrag}
            onDragStart={onDragStart}
            onDragEnd={onDragEnd}
            onDragOver={onDragOver}
            onDragLeave={onDragLeave}
            onDrop={onDrop}
          />
        );
      })}
    </div>
  );
}

/** One strip tab. A component (not map-inline JSX) so each session
 *  tab owns the ref that drives its animated icon from row hover. */
function TabItem({
  tab,
  active,
  enter,
  closing,
  label,
  closeLabel,
  segment = false,
  onActivate,
  onClose,
  onExited,
  dragSubject,
  dragSource,
  dropIntent,
  dropTarget,
  onPrepareDrag,
  onDragStart,
  onDragEnd,
  onDragOver,
  onDragLeave,
  onDrop,
}: {
  tab: CenterTab;
  active: boolean;
  enter: boolean;
  closing: boolean;
  label: string;
  closeLabel: string;
  segment?: boolean;
  onActivate: (tab: CenterTab) => void;
  onClose: (e: React.SyntheticEvent, tab: CenterTab) => void;
  onExited: (tab: CenterTab) => void;
  dragSubject: TabDragSubject;
  dragSource: boolean;
  dropIntent?: TabDropIntent["mode"];
  dropTarget: { tabId: string; groupId?: string; memberIndex?: number };
  onPrepareDrag: (subject: TabDragSubject) => void;
  onDragStart: (event: React.DragEvent<HTMLElement>) => void;
  onDragEnd: () => void;
  onDragOver: (
    event: React.DragEvent<HTMLElement>,
    target: { tabId: string; groupId?: string; memberIndex?: number },
  ) => void;
  onDragLeave: (event: React.DragEvent<HTMLElement>) => void;
  onDrop: (
    event: React.DragEvent<HTMLElement>,
    target: { tabId: string; groupId?: string; memberIndex?: number },
  ) => void;
}) {
  const iconRef = useRef<AnimatedNavIconHandle>(null);
  const tabRef = useRef<HTMLDivElement>(null);
  // 入场动画只在挂载那一次播；播完摘掉 class（.tabEnter 的 overflow:hidden
  // 不能留着，否则会裁掉活动 tab 的 fillet）。
  const [entering, setEntering] = useState(enter);
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
      draggable
      data-drag-source={dragSource || undefined}
      data-drop-intent={dropIntent}
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
      onPointerDown={(event) => {
        if (event.button === 0) onPrepareDrag(dragSubject);
      }}
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onDragOver={(event) => onDragOver(event, dropTarget)}
      onDragLeave={onDragLeave}
      onDrop={(event) => onDrop(event, dropTarget)}
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
        aria-selected={active}
        aria-label={label}
        tabIndex={active ? 0 : -1}
        onKeyDown={(e) => {
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
            <Globe size={13} />
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
        draggable={false}
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
