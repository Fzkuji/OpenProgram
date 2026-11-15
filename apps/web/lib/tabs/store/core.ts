import type { CenterTabGroup, CenterTabLayout } from "@/lib/tabs/center-tab-groups";
import { findCenterTabGroup } from "@/lib/tabs/center-tab-groups";
import { topLevelTabs } from "../../browser/web-tab/web-page-management";
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

// The tab the last insertNextTo placed, and the tab it was opened from.
let lastOpened: { from: string; id: string } | null = null;

/** A tab opened from another one goes to its right, after the tabs it opened before. */
export function insertNextTo(tabs: readonly CenterTab[], fromId: string | null | undefined, tab: CenterTab): CenterTab[] {
  const from = tabs.findIndex(item => item.id === fromId);
  if (from < 0 || !fromId) return [...tabs, tab];
  const previous = lastOpened?.from === fromId ? tabs.findIndex(item => item.id === lastOpened?.id) : -1;
  const at = previous > from ? previous + 1 : from + 1;
  lastOpened = { from: fromId, id: tab.id };
  return [...tabs.slice(0, at), tab, ...tabs.slice(at)];
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
  if (existing) {
    const updated = updateExisting ? updateExisting(existing) : existing;
    // A tab that went on to a management page (Scheduler, Settings…) shows its
    // own page again when it is opened again; Back still returns to that page.
    const next = updated.navigationRoute ? recordTabPage(updated, { ...updated, navigationRoute: undefined }) : updated;
    return commitCenterTabsState(s, {
      tabs: next === existing ? s.tabs : s.tabs.map(tab => tab.id === id ? next : tab),
      activeId: id,
    });
  }
  return commitCenterTabsState(s, { tabs: insertNextTo(s.tabs, s.activeId, make()), activeId: id });
}



/** The tab that takes focus after ``closedId`` leaves the strip: the pane
 *  beside it in its split, else the visible neighbour on the right, else on
 *  the left. ``tabs``/``groups`` describe the strip after the close. */
export function focusAfterClose(
  before: Pick<CenterTabsState, "tabs" | "groups" | "activeId">,
  closedId: string,
  tabs: readonly CenterTab[],
  groups: readonly CenterTabGroup[],
): string | null {
  const visibleTabs = topLevelTabs(tabs, groups);
  let activeId = before.activeId;
  const split = findCenterTabGroup(before.groups, closedId);
  if (activeId === closedId && split) {
    const survivors = split.memberIds.filter(member => member !== closedId && visibleTabs.some(tab => tab.id === member));
    activeId = survivors.includes(split.focusedId) ? split.focusedId : survivors[0] ?? activeId;
  }
  if (!visibleTabs.some((tab) => tab.id === activeId)) {
    const visibleIndex = topLevelTabs(before.tabs, before.groups).findIndex((tab) => tab.id === closedId);
    activeId = (visibleTabs[Math.max(0, visibleIndex)] ?? visibleTabs.at(-1))?.id ?? null;
  }
  return activeId;
}
