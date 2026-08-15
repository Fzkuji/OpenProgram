"use client";

/**
 * Tab context menu — the state machine behind right-click / Shift+F10 on a
 * strip tab, plus every action it offers (move left/right, close, close
 * others / to the right, new split view, remove from group, move to new
 * window).
 *
 * Two presentations share these actions: the in-DOM `role="menu"` panel the
 * strip renders (browser mode), and the desktop shell's top-layer overlay
 * menu, which must paint above native web-tab views. The overlay dispatches
 * back through `overlayMenuDispatchRef` so the once-registered listener
 * always sees the current render's handlers.
 */
import { useEffect, useRef, useState } from "react";

import {
  centerTabStripEntries,
  findCenterTabGroup,
  splitCandidates,
} from "@/lib/state/center-tab-groups";
import { useCenterTabs, type CenterTab } from "@/lib/state/center-tabs-store";
import { buildTransferPayload, desktopBridge } from "@/lib/desktop-bridge";
import { dragCoordinator } from "@/lib/tab-drag-coordinator";
import { useTranslation } from "@/lib/i18n";
import { activeThemeId } from "@/lib/prefs/theme-pref";
import {
  cancelCoordinator,
  menuDragSubject,
  removeReleaseListener,
} from "./tab-drag-subject";

export interface TabMenuState {
  tabId: string;
  left: number;
  top: number;
}

export interface TabMenuOptions {
  stripRef: React.RefObject<HTMLDivElement | null>;
  /** Close one strip entry through a single dirty-check + exit-animation
   *  transaction. A composite split entry contributes all of its members. */
  onTabsClose(event: React.SyntheticEvent, tabs: CenterTab[]): void;
  setFocusedTabId(tabId: string | null): void;
  setDragAnnouncement(message: string): void;
  clearDragState(): void;
  /** `targetBeforeId(targetTabId, after)` from the strip — shared with the
   *  drop path, so keyboard moves and drops land on the same insertion. */
  targetBeforeId(targetTabId: string, after: boolean): string | null;
}

export function useTabMenu({
  stripRef,
  onTabsClose,
  setFocusedTabId,
  setDragAnnouncement,
  clearDragState,
  targetBeforeId,
}: TabMenuOptions) {
  const { text } = useTranslation();
  const moveTab = useCenterTabs((s) => s.moveTab);
  const moveGroup = useCenterTabs((s) => s.moveGroup);
  const ungroupTab = useCenterTabs((s) => s.ungroupTab);

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
  const canMoveToNewWindow = Boolean(desktopBridge());
  // Desktop shell: the tab context menu is a top-layer overlay (see
  // openTabMenu). The subject tab and the dispatcher live in refs so the
  // once-registered onAction listener always sees the current render's
  // handlers. The channel is shared with MainMenu — only "tabmenu:*" ids
  // are ours.
  const overlayMenuTabIdRef = useRef<string | null>(null);
  const overlayMenuDispatchRef = useRef<(action: string) => void>(() => {});
  overlayMenuDispatchRef.current = (action) => {
    const tabId = overlayMenuTabIdRef.current;
    if (!tabId) return;
    switch (action) {
      case "move-left":
        moveMenuTab(tabId, -1);
        break;
      case "move-right":
        moveMenuTab(tabId, 1);
        break;
      case "close": {
        closeMenuTab(tabId);
        break;
      }
      case "close-others":
        closeMenuTabs(tabId, false);
        break;
      case "close-right":
        closeMenuTabs(tabId, true);
        break;
      case "split":
        openSplitPicker(tabId);
        break;
      case "ungroup":
        removeMenuTabFromGroup(tabId);
        break;
      case "new-window":
        moveMenuTabToNewWindow(tabId);
        break;
    }
  };
  useEffect(() => {
    const mainMenu = desktopBridge()?.mainMenu;
    if (!mainMenu) return;
    return mainMenu.onAction((id) => {
      if (!id.startsWith("tabmenu:")) return;
      overlayMenuDispatchRef.current(id.slice("tabmenu:".length));
    });
  }, []);

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
    // Desktop shell: the menu must paint above native web-tab views, which
    // always draw over the DOM — route it through the top-layer overlay
    // (same mechanism as the ⋮ main menu). Ids carry the "tabmenu:" prefix
    // so the shared main-menu:action channel disambiguates subscribers.
    const mainMenu = desktopBridge()?.mainMenu;
    if (mainMenu) {
      overlayMenuTabIdRef.current = tabId;
      mainMenu.open({
        anchor: { x: left, y: top, vw: window.innerWidth, vh: window.innerHeight },
        theme: activeThemeId(),
        items: [
          {
            id: "tabmenu:move-left",
            label: text("Move left", "向左移动"),
            disabled: !canMoveMenuTab(tabId, -1),
          },
          {
            id: "tabmenu:move-right",
            label: text("Move right", "向右移动"),
            disabled: !canMoveMenuTab(tabId, 1),
          },
          {
            id: "tabmenu:close",
            label: text("Close tab", "关闭标签页"),
          },
          {
            id: "tabmenu:close-others",
            label: text("Close other tabs", "关闭其他标签页"),
            disabled: closableTabsAround(tabId, false).length === 0,
          },
          {
            id: "tabmenu:close-right",
            label: text("Close tabs to the right", "关闭右侧标签页"),
            disabled: closableTabsAround(tabId, true).length === 0,
          },
          {
            id: "tabmenu:split",
            label: text("New split view with this tab", "与此标签页新建分屏"),
            disabled: !canOpenSplitPicker(tabId),
          },
          {
            id: "tabmenu:ungroup",
            label: text("Remove from group", "移出分组"),
            disabled: !findCenterTabGroup(useCenterTabs.getState().groups, tabId),
          },
          {
            id: "tabmenu:new-window",
            label: text("Move to new window", "移至新窗口"),
            disabled: !canMoveToNewWindow,
          },
        ],
      });
      return;
    }
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
    const entries = centerTabStripEntries({
      tabIds: state.tabs.map((tab) => tab.id),
      groups: state.groups,
    });
    const index = entries.findIndex((entry) => entry.kind === "group"
      ? entry.group.memberIds.includes(tabId)
      : entry.tabId === tabId);
    return index >= 0 && Boolean(entries[index + direction]);
  }

  function moveMenuTab(tabId: string, direction: -1 | 1) {
    const state = useCenterTabs.getState();
    const sourceGroup = findCenterTabGroup(state.groups, tabId);
    if (sourceGroup) {
      const entries = centerTabStripEntries({
        tabIds: state.tabs.map((tab) => tab.id),
        groups: state.groups,
      });
      const sourceIndex = entries.findIndex(
        (entry) => entry.kind === "group" && entry.group.id === sourceGroup.id,
      );
      const target = entries[sourceIndex + direction];
      if (!target) return;
      const targetId = target.kind === "group"
        ? target.group.memberIds[0]
        : target.tabId;
      moveGroup(
        sourceGroup.id,
        direction < 0 ? targetId : targetBeforeId(targetId, true),
      );
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
    if (findCenterTabGroup(state.groups, tabId)) return false;
    return splitCandidates(state.tabs, state.groups, tabId).length > 0;
  }

  /** Chrome's "New Split View with Current Tab": close the menu and show
   *  the tab picker; the actual grouping happens when a tab is chosen. */
  function openSplitPicker(tabId: string) {
    setTabMenu(null);
    setSplitPickerTabId(tabId);
  }

  function removeMenuTabFromGroup(tabId: string) {
    const group = findCenterTabGroup(useCenterTabs.getState().groups, tabId);
    if (!group) return;
    for (const memberId of group.memberIds.slice(0, -1)) ungroupTab(memberId);
    finishMenuAction(tabId, text("Tab removed from group", "标签已移出分组"));
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

  function tabsForEntry(tabId: string): CenterTab[] {
    const state = useCenterTabs.getState();
    const group = findCenterTabGroup(state.groups, tabId);
    const ids = group?.memberIds ?? [tabId];
    return ids.flatMap((id) => {
      const tab = state.tabs.find((candidate) => candidate.id === id);
      return tab ? [tab] : [];
    });
  }

  function closeMenuTab(tabId: string) {
    const victims = tabsForEntry(tabId);
    setTabMenu(null);
    if (victims.length > 0) {
      onTabsClose(
        { stopPropagation: () => {} } as React.SyntheticEvent,
        victims,
      );
    }
  }

  /** Tabs the "close others / close to the right" menu items would act
   *  on, in strip-entry order. A split group is one entry, so its members
   *  are retained or closed together. */
  function closableTabsAround(tabId: string, after: boolean): CenterTab[] {
    const state = useCenterTabs.getState();
    const entries = centerTabStripEntries({
      tabIds: state.tabs.map((tab) => tab.id),
      groups: state.groups,
    });
    const at = entries.findIndex((entry) => entry.kind === "group"
      ? entry.group.memberIds.includes(tabId)
      : entry.tabId === tabId);
    if (at < 0) return [];
    const victims = after
      ? entries.slice(at + 1)
      : entries.filter((_, index) => index !== at);
    const ids = victims.flatMap((entry) =>
      entry.kind === "group" ? entry.group.memberIds : [entry.tabId],
    );
    return ids
      .map((id) => state.tabs.find((tab) => tab.id === id))
      .filter((tab): tab is CenterTab => !!tab);
  }

  /** Bulk close is one transaction so a composite entry cannot close only
   *  one member after a later dirty prompt is cancelled. */
  function closeMenuTabs(tabId: string, after: boolean) {
    const victims = closableTabsAround(tabId, after);
    setTabMenu(null);
    returnFocusToMenuInvoker(tabId);
    if (victims.length > 0) onTabsClose(
      { stopPropagation: () => {} } as React.SyntheticEvent,
      victims,
    );
  }

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
  }, [tabMenu, stripRef]);

  return {
    tabMenu,
    setTabMenu,
    splitPickerTabId,
    setSplitPickerTabId,
    splitPickerHost,
    canMoveToNewWindow,
    openTabMenu,
    canMoveMenuTab,
    moveMenuTab,
    canOpenSplitPicker,
    openSplitPicker,
    removeMenuTabFromGroup,
    moveMenuTabToNewWindow,
    closableTabsAround,
    closeMenuTabs,
    closeMenuTab,
    returnFocusToMenuInvoker,
    finishMenuAction,
  };
}
