import { findCenterTabGroup, ungroupCenterTab } from "@/lib/tabs/center-tab-groups";
import { hostnameOf, webTabId } from "@/lib/tabs/center-tab-ids";
import { clampSplitRatio } from "@/lib/tabs/center-tabs-persistence";
import type { StoreApi } from "zustand";
import { commitCenterTabsState, tabsForLayout } from "./core";
import type { CenterTabsState } from "./types";

export function splitActions(set: StoreApi<CenterTabsState>["setState"], get: StoreApi<CenterTabsState>["getState"]): Pick<CenterTabsState, "openWebTabInSplit" | "setSplitWebTab" | "setSplitRatio"> {
  return {
    openWebTabInSplit: (url) => {
      let id = webTabId(url);
      set((s) => {
        const ownedGroup = findCenterTabGroup(s.groups, id);
        if (ownedGroup && !ownedGroup.memberIds.includes(s.activeId ?? "")) {
          const ownedWeb = s.tabs.find((tab) => tab.id === id && tab.kind === "web");
          const tabs = ownedWeb?.url === url
            ? s.tabs
            : s.tabs.map((tab) => tab.id === id
              ? {
                ...tab,
                url,
                title: hostnameOf(url),
                faviconUrl: undefined,
              }
              : tab);
          const ownerActiveId = ownedGroup.memberIds.find((memberId) =>
            s.tabs.some((tab) => tab.id === memberId && tab.kind === "session"),
          ) ?? ownedGroup.focusedId;
          return commitCenterTabsState(s, {
            tabs,
            activeId: ownerActiveId,
            splitWebTabId: id,
          });
        }
        const activeGroup = s.activeId
          ? findCenterTabGroup(s.groups, s.activeId)
          : undefined;
        const groupedWeb = activeGroup?.memberIds
          .map((memberId) => s.tabs.find((tab) => tab.id === memberId))
          .find((tab) => tab?.kind === "web");
        if (groupedWeb) {
          id = groupedWeb.id;
          const tabs = groupedWeb.url === url
            ? s.tabs
            : s.tabs.map((tab) => tab.id === groupedWeb.id
              ? {
                ...tab,
                url,
                title: hostnameOf(url),
                faviconUrl: undefined,
              }
              : tab);
          return commitCenterTabsState(s, {
            tabs,
            splitWebTabId: groupedWeb.id,
          });
        }
        const existing = s.tabs.find((tab) => tab.id === id);
        const tabs = !existing
          ? [...s.tabs, { id, kind: "web" as const, title: hostnameOf(url), url }]
          : existing.url !== url
            ? s.tabs.map((tab) =>
              tab.id === id ? { ...tab, url, title: hostnameOf(url) } : tab,
            )
            : s.tabs;
        return commitCenterTabsState(s, { tabs, splitWebTabId: id });
      });
      return id;
    },

    setSplitWebTab: (id) =>
      set((s) => {
        const tabId =
          id && s.tabs.some((tab) => tab.id === id && tab.kind === "web")
            ? id
            : null;
        if (tabId === null) {
          if (s.splitWebTabId === null) return {};
          const layout = ungroupCenterTab({
            tabIds: s.tabs.map((tab) => tab.id),
            groups: s.groups,
          }, s.splitWebTabId);
          return commitCenterTabsState(s, {
            tabs: tabsForLayout(s.tabs, layout),
            groups: layout.groups,
            splitWebTabId: null,
          });
        }
        const active = s.tabs.find((tab) => tab.id === s.activeId);
        const group = active?.kind === "session"
          ? findCenterTabGroup(s.groups, active.id)
          : undefined;
        if (tabId === s.splitWebTabId && group?.memberIds.includes(tabId)
          && group.visibleIds.includes(active!.id)
          && group.visibleIds.includes(tabId)) return {};
        let groups = s.groups;
        let tabs = s.tabs;
        // Session already in a pair (usually session+file): evict the
        // other member so session+web can form. The evicted tab stays
        // in the strip. A full group cannot accept a third pane.
        if (active?.kind === "session" && group && !group.memberIds.includes(tabId)) {
          let layout = {
            tabIds: s.tabs.map((tab) => tab.id),
            groups: s.groups,
          };
          for (const memberId of group.memberIds) {
            if (memberId !== active.id) {
              layout = ungroupCenterTab(layout, memberId);
            }
          }
          groups = layout.groups;
          tabs = tabsForLayout(s.tabs, layout);
        }
        return commitCenterTabsState(s, { tabs, groups, splitWebTabId: tabId });
      }),

    setSplitRatio: (ratio) =>
      set((s) => {
        const splitRatio = clampSplitRatio(ratio);
        if (splitRatio === s.splitRatio) return {};
        return commitCenterTabsState(s, { splitRatio });
      })
  };
}
