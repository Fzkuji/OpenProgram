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
import { createPortal } from "react-dom";
import { usePathname, useRouter } from "next/navigation";
import { Bookmark, CirclePlus, FileText, Globe, GripVertical, History, Plus, SquareArrowOutUpRight, X } from "lucide-react";

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
  MAX_CENTER_TAB_GROUP_MEMBERS,
  splitCandidates,
  type CenterTabGroup,
} from "@/lib/state/center-tab-groups";
import {
  DETACH_HYSTERESIS_PX,
  DRAG_START_THRESHOLD_PX,
  dragCoordinator,
  resolveTabDropIntent,
  SWAP_OVERLAP_RATIO,
  type TabDragSubject,
  type TabDropIntent,
} from "@/lib/tab-drag-coordinator";
import {
  buildTransferPayload,
  desktopBridge,
} from "@/lib/desktop-bridge";
import { builtinPageLabel } from "./builtin-page-label";
import { MainMenu } from "./main-menu";
import { SplitViewPicker } from "./split-view-picker";
import { deleteAttachments } from "@/components/chat/composer/attach/attach-idb";
import {
  dropDraftChannelChoice,
  type DraftChannelChoiceHost,
} from "@/lib/runtime-bridge/draft-channel-choice";
import { fileDraftKey, fileDrafts } from "@/lib/state/files-shared";
import { useSessionStore } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import styles from "./center-tabs.module.css";
import {
  computeLiveShifts,
  collectPointerDropTargets,
  shiftStyle,
  visibleStripBounds,
  slotOverlapRatio,
  pickPointerDropTarget,
  type PointerDropTarget,
} from "./tab-strip-geometry";

let removePreparedReleaseListener: (() => void) | null = null;

interface TabMenuState {
  tabId: string;
  left: number;
  top: number;
}

function removeReleaseListener() {
  removePreparedReleaseListener?.();
  removePreparedReleaseListener = null;
}

/** Cancel the local coordinator AND its prepared main-process token. */
function cancelCoordinator() {
  const cancelled = dragCoordinator.cancel();
  if (cancelled?.transferToken) {
    void desktopBridge()?.tabTransfer.cancel(cancelled.transferToken);
  }
  return cancelled;
}

function snapshotTabDragSubject(subject: TabDragSubject): TabDragSubject {
  if (subject.kind === "tab") return { kind: "tab", tabIds: [subject.tabIds[0]] };
  if (subject.kind === "segment") {
    return {
      ...subject,
      tabIds: [subject.tabIds[0]],
      sourceGroup: {
        ...subject.sourceGroup,
        memberIds: [...subject.sourceGroup.memberIds],
        visibleIds: [...subject.sourceGroup.visibleIds],
      },
    };
  }
  return {
    ...subject,
    tabIds: [...subject.tabIds],
    sourceGroup: {
      ...subject.sourceGroup,
      memberIds: [...subject.sourceGroup.memberIds],
      visibleIds: [...subject.sourceGroup.visibleIds],
    },
  };
}

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

function isChatRoute(pathname: string) {
  return pathname === "/chat" || pathname.startsWith("/s/");
}

function labelOf(
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

/** Stable empty shift map — used while detaching, when neighbours must not
 *  move to fill the leaving tab's slot. A shared frozen instance avoids a new
 *  Map every render. */
const EMPTY_SHIFTS: ReadonlyMap<string, number> = new Map();

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
  const mergeGroup = useCenterTabs((s) => s.mergeGroup);
  const ungroupTab = useCenterTabs((s) => s.ungroupTab);

  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const conversations = useSessionStore((s) => s.conversations);
  const [sessionActivationRequest, setSessionActivationRequest] = useState(0);
  const [focusedTabId, setFocusedTabId] = useState<string | null>(activeId);
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
  const [detachCueHost, setDetachCueHost] = useState<Element | null>(null);
  useEffect(() => {
    setDetachCueHost(detachCue ? document.querySelector(".center-body") : null);
  }, [detachCue !== null]);
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
  const [dragAnnouncement, setDragAnnouncement] = useState("");
  const [tabMenu, setTabMenu] = useState<TabMenuState | null>(null);
  const [splitPickerTabId, setSplitPickerTabId] = useState<string | null>(null);
  // Portal host for the split picker, resolved after mount so the first
  // (server) render stays deterministic.
  const [splitPickerHost, setSplitPickerHost] = useState<Element | null>(null);
  useEffect(() => {
    setSplitPickerHost(
      splitPickerTabId ? document.querySelector(".center-body") : null,
    );
  }, [splitPickerTabId]);
  // Mirror of tabMenu for the pointer handlers, which run from listeners
  // registered on an earlier render and would otherwise read a stale value.
  const tabMenuRef = useRef<TabMenuState | null>(null);
  tabMenuRef.current = tabMenu;
  // Tab id activated by the current pointerdown, consumed by the click
  // that follows it (see onTabPointerDown / onTabClickFromPointer).
  const activatedOnPressRef = useRef<string | null>(null);
  const stripRef = useRef<HTMLDivElement>(null);
  const tabsFlowRef = useRef<HTMLDivElement>(null);
  const canMoveToNewWindow = Boolean(desktopBridge());

  // The ＋ no longer needs rail alignment: the main menu button owns the
  // reserved right column, so ＋ is a plain flex item after the tab flow
  // and its separator geometry is static (see center-tabs.module.css).

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

  useEffect(() => {
    setFocusedTabId(activeId);
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

  function returnFocusToMenuInvoker(tabId: string) {
    requestAnimationFrame(() => {
      const items = stripRef.current?.querySelectorAll<HTMLElement>(
        '[role="tab"][data-tab-id]',
      );
      const invoker = items
        ? Array.from(items).find((item) => item.dataset.tabId === tabId)
        : undefined;
      invoker?.focus();
    });
  }

  function finishMenuAction(tabId: string, announcement: string) {
    setTabMenu(null);
    setFocusedTabId(tabId);
    setDragAnnouncement(announcement);
    returnFocusToMenuInvoker(tabId);
  }

  function openTabMenu(
    event: React.MouseEvent<HTMLElement> | React.KeyboardEvent<HTMLElement>,
    tabId: string,
  ) {
    event.preventDefault();
    event.stopPropagation();
    const rect = event.currentTarget.getBoundingClientRect();
    const left = "clientX" in event ? event.clientX : rect.left;
    const top = "clientY" in event ? event.clientY : rect.bottom;
    setFocusedTabId(tabId);
    setTabMenu({
      tabId,
      left: Math.min(Math.max(8, left), Math.max(8, window.innerWidth - 208)),
      top: Math.min(Math.max(8, top), Math.max(8, window.innerHeight - 204)),
    });
    requestAnimationFrame(() => {
      stripRef.current
        ?.querySelector<HTMLButtonElement>('[role="menu"] button:not(:disabled)')
        ?.focus();
    });
  }

  function canMoveMenuTab(tabId: string, direction: -1 | 1) {
    const state = useCenterTabs.getState();
    if (findCenterTabGroup(state.groups, tabId)) return true;
    const entries = centerTabStripEntries({
      tabIds: state.tabs.map((tab) => tab.id),
      groups: state.groups,
    });
    const index = entries.findIndex(
      (entry) => entry.kind === "tab" && entry.tabId === tabId,
    );
    return index >= 0 && Boolean(entries[index + direction]);
  }

  function moveMenuTab(tabId: string, direction: -1 | 1) {
    const state = useCenterTabs.getState();
    const sourceGroup = findCenterTabGroup(state.groups, tabId);
    if (sourceGroup) {
      const memberIndex = sourceGroup.memberIds.indexOf(tabId);
      const nextIndex = memberIndex + direction;
      if (nextIndex >= 0 && nextIndex < sourceGroup.memberIds.length) {
        moveGroupMember(sourceGroup.id, tabId, nextIndex);
      } else {
        const beforeId = direction < 0
          ? sourceGroup.memberIds[1] ?? null
          : targetBeforeId(sourceGroup.memberIds.at(-1) ?? tabId, true);
        ungroupTab(tabId, beforeId);
      }
    } else {
      const entries = centerTabStripEntries({
        tabIds: state.tabs.map((tab) => tab.id),
        groups: state.groups,
      });
      const sourceIndex = entries.findIndex(
        (entry) => entry.kind === "tab" && entry.tabId === tabId,
      );
      const target = entries[sourceIndex + direction];
      if (!target) return;
      const targetId = target.kind === "group"
        ? target.group.memberIds[0]
        : target.tabId;
      moveTab(
        tabId,
        direction < 0 ? targetId : targetBeforeId(targetId, true),
      );
    }
    finishMenuAction(tabId, text("Tab reordered", "标签顺序已调整"));
  }

  /** The split entry is available whenever this window has another tab
   *  to pair with — Chrome keeps it enabled and lets the picker decide. */
  function canOpenSplitPicker(tabId: string) {
    const state = useCenterTabs.getState();
    return splitCandidates(state.tabs, state.groups, tabId).length > 0;
  }

  /** Chrome's "New Split View with Current Tab": close the menu and show
   *  the tab picker; the actual grouping happens when a tab is chosen. */
  function openSplitPicker(tabId: string) {
    setTabMenu(null);
    setSplitPickerTabId(tabId);
  }

  function removeMenuTabFromGroup(tabId: string) {
    if (!findCenterTabGroup(useCenterTabs.getState().groups, tabId)) return;
    ungroupTab(tabId);
    finishMenuAction(tabId, text("Tab removed from group", "标签已移出分组"));
  }

  function menuDragSubject(tabId: string): TabDragSubject | null {
    const state = useCenterTabs.getState();
    if (!state.tabs.some((tab) => tab.id === tabId)) return null;
    const sourceGroup = findCenterTabGroup(state.groups, tabId);
    if (!sourceGroup) return { kind: "tab", tabIds: [tabId] };
    return snapshotTabDragSubject({
      kind: "segment",
      tabIds: [tabId],
      sourceGroup,
      memberIndex: sourceGroup.memberIds.indexOf(tabId),
    });
  }

  // Same coordinator + token lifecycle as pointer drag: synchronous
  // main-process prepare on the menu action, then detach into a new
  // window (the dragend outside-drop path, minus the drag).
  function moveMenuTabToNewWindow(tabId: string) {
    const subject = menuDragSubject(tabId);
    const bridge = desktopBridge();
    if (!subject || !bridge) return;
    removeReleaseListener();
    cancelCoordinator();
    const payload = buildTransferPayload(subject, bridge.windowId);
    const token = (payload && bridge.tabTransfer.prepare(payload)) || null;
    if (!token) {
      finishMenuAction(tabId, text("Tab move cancelled", "标签移动已取消"));
      return;
    }
    dragCoordinator.prepare({
      subject,
      transferToken: token,
      started: false,
      cancelled: false,
      committed: false,
    });
    dragCoordinator.start();
    dragCoordinator.clear();
    clearDragState();
    setTabMenu(null);
    setFocusedTabId(tabId);
    returnFocusToMenuInvoker(tabId);
    bridge.tabTransfer.detach(token).then(
      (detachedWindowId) =>
        setDragAnnouncement(
          detachedWindowId
            ? text("Tab moved to new window", "标签已移至新窗口")
            : text("Tab move cancelled", "标签移动已取消"),
        ),
      () =>
        setDragAnnouncement(text("Tab move cancelled", "标签移动已取消")),
    );
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
    setFocusedTabId(items[nextIndex].dataset.tabId ?? null);
    items[nextIndex].focus();
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
    removePreparedReleaseListener = () =>
      window.removeEventListener("pointerup", cancelUnstarted);
  }

  // ---- Pointer-driven drag (Chrome-style) --------------------------
  // The pressed tab element itself follows the pointer via transform —
  // no HTML5 drag, no system ghost, no residue at the origin slot.
  const pointerDragRef = useRef<PointerDragState | null>(null);

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

  function targetBeforeId(targetTabId: string, after: boolean): string | null {
    const state = useCenterTabs.getState();
    const targetGroup = findCenterTabGroup(state.groups, targetTabId);
    if (!after) return targetGroup?.memberIds[0] ?? targetTabId;
    const lastTargetId = targetGroup?.memberIds.at(-1) ?? targetTabId;
    const targetIndex = state.tabs.findIndex((tab) => tab.id === lastTargetId);
    return state.tabs[targetIndex + 1]?.id ?? null;
  }

  function isFourthMemberRejection(
    subject: TabDragSubject,
    intent: TabDropIntent,
  ) {
    if (intent.mode !== "merge") return false;
    const targetGroup = findCenterTabGroup(
      useCenterTabs.getState().groups,
      intent.targetTabId,
    );
    if (subject.kind === "group") {
      if (targetGroup?.id === subject.sourceGroup.id) return false;
      return (targetGroup?.memberIds.length ?? 1) + subject.tabIds.length
        > MAX_CENTER_TAB_GROUP_MEMBERS;
    }
    if (targetGroup?.memberIds.includes(subject.tabIds[0])) return false;
    return (targetGroup?.memberIds.length ?? 1) + 1
      > MAX_CENTER_TAB_GROUP_MEMBERS;
  }

  function applyDrop(prepared: NonNullable<ReturnType<typeof dragCoordinator.current>>, intent: TabDropIntent) {
    const subject = prepared.subject;
    if (intent.mode === "merge") {
      if (subject.kind === "group") {
        return mergeGroup(
          subject.sourceGroup.id,
          intent.targetTabId,
          intent.memberIndex ?? 1,
        );
      }
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
      const menuTabId = tabMenu?.tabId;
      const cancelled = Boolean(dragCoordinator.current() || menuTabId);
      teardownPointerDrag(); // return-home animation for a live pointer drag
      cancelCoordinator();
      removeReleaseListener();
      setDraggedIds(new Set());
      setDropMarker(null);
      setTabMenu(null);
      if (cancelled) {
        setDragAnnouncement(text("Tab move cancelled", "标签移动已取消"));
      }
      if (menuTabId) returnFocusToMenuInvoker(menuTabId);
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

  // Dismiss the tab context menu on any interaction outside it. The menu
  // stops propagation of its own pointerdown, so anything arriving here
  // is genuinely outside. Capture phase means a tab's own pointerdown
  // handler cannot consume the dismissal first.
  useEffect(() => {
    if (!tabMenu) return;
    const dismiss = () => setTabMenu(null);
    const onOutsidePointerDown = (e: PointerEvent) => {
      const menu = stripRef.current?.querySelector('[role="menu"]');
      if (menu && e.target instanceof Node && menu.contains(e.target)) return;
      dismiss();
    };
    document.addEventListener("pointerdown", onOutsidePointerDown, true);
    window.addEventListener("blur", dismiss);
    // Scrolling the tab flow (or anything else) moves the anchor out from
    // under the fixed-position menu, so close instead of letting it drift.
    window.addEventListener("scroll", dismiss, true);
    window.addEventListener("resize", dismiss);
    return () => {
      document.removeEventListener("pointerdown", onOutsidePointerDown, true);
      window.removeEventListener("blur", dismiss);
      window.removeEventListener("scroll", dismiss, true);
      window.removeEventListener("resize", dismiss);
    };
  }, [tabMenu]);

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
                onOpenMenu={openTabMenu}
                onClose={onTabClose}
                onExited={finishClose}
                shiftX={liveShifts.get(entry.id) ?? 0}
                onDragPointerDown={onTabPointerDown}
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
              tabStop={tab.id === focusedTabId}
              enter={enteringIds.has(tab.id)}
              closing={closingIds.has(tab.id)}
              label={labelOf(tab, t, text)}
              closeLabel={text("Close tab", "关闭标签")}
              onActivate={onTabClickFromPointer}
              onFocusTab={setFocusedTabId}
              onOpenMenu={openTabMenu}
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
        <div
          className={styles.tabMenu}
          role="menu"
          aria-label={text("Tab actions", "标签操作")}
          style={{ left: tabMenu.left, top: tabMenu.top }}
          onPointerDown={(event) => event.stopPropagation()}
        >
          <button
            type="button"
            role="menuitem"
            className={styles.tabMenuItem}
            disabled={!canMoveMenuTab(tabMenu.tabId, -1)}
            onClick={() => moveMenuTab(tabMenu.tabId, -1)}
          >
            {text("Move left", "向左移动")}
          </button>
          <button
            type="button"
            role="menuitem"
            className={styles.tabMenuItem}
            disabled={!canMoveMenuTab(tabMenu.tabId, 1)}
            onClick={() => moveMenuTab(tabMenu.tabId, 1)}
          >
            {text("Move right", "向右移动")}
          </button>
          <button
            type="button"
            role="menuitem"
            className={styles.tabMenuItem}
            disabled={!canOpenSplitPicker(tabMenu.tabId)}
            onClick={() => openSplitPicker(tabMenu.tabId)}
          >
            {text("New split view with this tab", "与此标签页新建分屏")}
          </button>
          <button
            type="button"
            role="menuitem"
            className={styles.tabMenuItem}
            disabled={!findCenterTabGroup(groups, tabMenu.tabId)}
            onClick={() => removeMenuTabFromGroup(tabMenu.tabId)}
          >
            {text("Remove from group", "移出分组")}
          </button>
          <button
            type="button"
            role="menuitem"
            className={styles.tabMenuItem}
            disabled={!canMoveToNewWindow}
            onClick={() => moveMenuTabToNewWindow(tabMenu.tabId)}
          >
            {text("Move to new window", "移至新窗口")}
          </button>
        </div>
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
                returnFocusToMenuInvoker(subject);
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
                returnFocusToMenuInvoker(subject);
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

function CompoundTabItem({
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
function TabItem({
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
            tab.page === "history" ? <History size={13} /> : <Bookmark size={13} />
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
