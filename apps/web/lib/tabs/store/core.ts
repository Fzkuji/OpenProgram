import type { CenterTabLayout } from "@/lib/tabs/center-tab-groups";
import { findCenterTabGroup } from "@/lib/tabs/center-tab-groups";
import type { CenterTabsPersistedState } from "@/lib/tabs/center-tabs-persistence";
import { normalizeCenterTabsPayload, orderTabs, persistCenterTabsPayload, persistedState, replaceGroupTabId } from "@/lib/tabs/center-tabs-persistence";
import { recordTabPage } from "../navigation/page-history";
import { fileHistoryFor } from "../navigation/selectors";
import type { CenterTab, CenterTabsState } from "./types";
export function commitCenterTabsState(
  state: CenterTabsState,
  patch: Partial<CenterTabsPersistedState>,
): CenterTabsPersistedState & Pick<CenterTabsState, "navigationRoute" | "fileNavigationHistory" | "fileNavigationRestore"> {
  const payload = normalizeCenterTabsPayload({
    tabs: patch.tabs ?? state.tabs,
    activeId: patch.activeId === undefined ? state.activeId : patch.activeId,
    groups: patch.groups ?? state.groups,
    splitWebTabId: patch.splitWebTabId === undefined ? state.splitWebTabId : patch.splitWebTabId,
    splitRatio: patch.splitRatio ?? state.splitRatio,
  });
  persistCenterTabsPayload(payload);
  const before = state.tabs.find(tab => tab.id === state.activeId);
  const after = payload.tabs.find(tab => tab.id === payload.activeId);
  const changedPage = before?.id !== after?.id || before?.pageHistory?.index !== after?.pageHistory?.index;
  return {
    ...persistedState(payload), navigationRoute: after?.navigationRoute,
    fileNavigationHistory: fileHistoryFor(after),
    fileNavigationRestore: changedPage ? after?.fileNavigationSnapshot ?? null : state.fileNavigationRestore
  };
}

/** Content identities can be displayed independently in multiple tab slots. */
export function independentTab(tab: CenterTab, tabs: readonly CenterTab[], replacingId?: string): CenterTab {
  return tabs.some(existing => existing.id === tab.id && existing.id !== replacingId)
    ? { ...tab, id: `${tab.id}:tab:${crypto.randomUUID()}` } : tab;
}

export function tabsForLayout(
  tabs: readonly CenterTab[],
  layout: CenterTabLayout,
): CenterTab[] {
  return orderTabs(tabs, layout.tabIds);
}

export function detachesSplitPair(state: CenterTabsState, tabId: string): boolean {
  const splitId = state.splitWebTabId;
  if (!splitId) return false;
  if (tabId === splitId) return true;
  return !!findCenterTabGroup(state.groups, tabId)?.memberIds.includes(splitId);
}

export function focusOrCreate(
  s: CenterTabsState,
  id: string,
  make: () => CenterTab,
  replaceable: string[],
  updateExisting?: (tab: CenterTab) => CenterTab,
): Partial<CenterTabsState> {
  const active = s.tabs.find(t => t.id === s.activeId);
  const replace = active && (active.kind === "ntp" || (active.kind === "builtin" && active.page === "browser")
    || replaceable.includes(active.id));
  if (replace) {
    const next = independentTab(make(), s.tabs, active.id);
    const tabs = s.tabs.map(tab => tab.id === active.id ? recordTabPage(active, next) : tab);
    return commitCenterTabsState(s, { tabs, activeId: next.id, groups: replaceGroupTabId(s.groups, active.id, next.id) });
  }
  const existing = s.tabs.find(tab => tab.id === id);
  if (existing) return commitCenterTabsState(s, {
    tabs: updateExisting ? s.tabs.map(tab => tab.id === id ? updateExisting(tab) : tab) : s.tabs,
    activeId: id,
  });
  return commitCenterTabsState(s, { tabs: [...s.tabs, make()], activeId: id });
}


