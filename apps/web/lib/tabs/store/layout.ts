import { findCenterTabGroup, focusCenterTabGroupMember, groupCenterTabs, mergeCenterTabGroup, moveCenterTab, moveCenterTabGroup, moveCenterTabGroupMember, ungroupCenterTab } from "@/lib/tabs/center-tab-groups";
import type { StoreApi } from "zustand";
import { commitCenterTabsState, detachesSplitPair, tabsForLayout } from "./core";
import type { CenterTabsState } from "./types";

export function layoutActions(set: StoreApi<CenterTabsState>["setState"], get: StoreApi<CenterTabsState>["getState"]): Pick<CenterTabsState, "setActive" | "moveTab" | "moveGroup" | "moveGroupMember" | "groupTab" | "mergeGroup" | "ungroupTab" | "focusGroupMember"> {
  return {
    setActive: (id) =>
      set((s) => {
        if (!s.tabs.some((tab) => tab.id === id)) return {};
        const group = findCenterTabGroup(s.groups, id);
        if (s.activeId === id && (!group || group.focusedId === id)) return {};
        const layout = group
          ? focusCenterTabGroupMember({
            tabIds: s.tabs.map((tab) => tab.id),
            groups: s.groups,
          }, group.id, id)
          : { tabIds: s.tabs.map((tab) => tab.id), groups: s.groups };
        const next = commitCenterTabsState(s, {
          activeId: id,
          tabs: tabsForLayout(s.tabs, layout),
          groups: layout.groups,
        });
        return next;
      }),

    moveTab: (id, beforeId) =>
      set((s) => {
        const splitWebTabId = detachesSplitPair(s, id)
          ? null
          : s.splitWebTabId;
        const layout = moveCenterTab({
          tabIds: s.tabs.map((tab) => tab.id),
          groups: s.groups,
        }, id, beforeId);
        if (layout.tabIds.every((tabId, index) => tabId === s.tabs[index]?.id)
          && layout.groups === s.groups) return {};
        return commitCenterTabsState(s, {
          tabs: tabsForLayout(s.tabs, layout),
          groups: layout.groups,
          splitWebTabId,
        });
      }),

    moveGroup: (groupId, beforeId) =>
      set((s) => {
        const layout = moveCenterTabGroup({
          tabIds: s.tabs.map((tab) => tab.id),
          groups: s.groups,
        }, groupId, beforeId);
        if (layout.tabIds.every((tabId, index) => tabId === s.tabs[index]?.id)) {
          return {};
        }
        return commitCenterTabsState(s, {
          tabs: tabsForLayout(s.tabs, layout),
          groups: layout.groups,
        });
      }),

    moveGroupMember: (groupId, memberId, toIndex) =>
      set((s) => {
        const layout = moveCenterTabGroupMember({
          tabIds: s.tabs.map((tab) => tab.id),
          groups: s.groups,
        }, groupId, memberId, toIndex);
        const prior = findCenterTabGroup(s.groups, memberId);
        const next = findCenterTabGroup(layout.groups, memberId);
        if (!prior || !next || next.memberIds.every(
          (id, index) => id === prior.memberIds[index],
        )) return {};
        return commitCenterTabsState(s, {
          tabs: tabsForLayout(s.tabs, layout),
          groups: layout.groups,
        });
      }),

    groupTab: (sourceId, targetId, memberIndex, groupId) => {
      let accepted = false;
      set((s) => {
        const pairAlreadyGrouped = s.groups.some((group) =>
          group.memberIds.includes(sourceId) && group.memberIds.includes(targetId),
        );
        const result = groupCenterTabs({
          tabIds: s.tabs.map((tab) => tab.id),
          groups: s.groups,
        }, sourceId, targetId, memberIndex, groupId ?? `g:${crypto.randomUUID()}`);
        accepted = result.accepted;
        if (!accepted) return {};
        return commitCenterTabsState(s, {
          tabs: tabsForLayout(s.tabs, result.layout),
          groups: result.layout.groups,
          activeId: sourceId,
          splitRatio: pairAlreadyGrouped ? s.splitRatio : 0.5,
        });
      });
      return accepted;
    },

    mergeGroup: (sourceGroupId, targetId, memberIndex) => {
      let accepted = false;
      set((s) => {
        const currentLayout = {
          tabIds: s.tabs.map((tab) => tab.id),
          groups: s.groups,
        };
        const result = mergeCenterTabGroup(
          currentLayout,
          sourceGroupId,
          targetId,
          memberIndex,
        );
        accepted = result.accepted;
        if (!accepted || result.layout === currentLayout) return {};
        return commitCenterTabsState(s, {
          tabs: tabsForLayout(s.tabs, result.layout),
          groups: result.layout.groups,
        });
      });
      return accepted;
    },

    ungroupTab: (id, beforeId = null) =>
      set((s) => {
        const splitWebTabId = detachesSplitPair(s, id)
          ? null
          : s.splitWebTabId;
        const prior = findCenterTabGroup(s.groups, id);
        const layout = ungroupCenterTab({
          tabIds: s.tabs.map((tab) => tab.id),
          groups: s.groups,
        }, id, beforeId);
        if (!prior && beforeId === null) return {};
        return commitCenterTabsState(s, {
          tabs: tabsForLayout(s.tabs, layout),
          groups: layout.groups,
          splitWebTabId,
        });
      }),

    focusGroupMember: (groupId, memberId) =>
      set((s) => {
        const group = s.groups.find((candidate) => candidate.id === groupId);
        if (!group?.memberIds.includes(memberId)) return {};
        const layout = focusCenterTabGroupMember({
          tabIds: s.tabs.map((tab) => tab.id),
          groups: s.groups,
        }, groupId, memberId);
        return commitCenterTabsState(s, {
          activeId: memberId,
          tabs: tabsForLayout(s.tabs, layout),
          groups: layout.groups,
        });
      })
  };
}
