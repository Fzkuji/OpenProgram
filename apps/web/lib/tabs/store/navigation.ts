import { findCenterTabGroup, normalizeCenterTabLayout } from "@/lib/tabs/center-tab-groups";
import { nextNtpId } from "@/lib/tabs/center-tab-ids";
import { replaceGroupTabId } from "@/lib/tabs/center-tabs-persistence";
import type { StoreApi } from "zustand";
import { navigateTabRoute } from "../navigation/route";
import { pageHistory, recordTabPage, restoreTabPage, tabPage } from "../navigation/page-history";
import { navigationTarget } from "../navigation/selectors";
import { commitCenterTabsState, focusAfterClose, independentTab } from "./core";
import type { CenterTab, CenterTabsState } from "./types";
import { forgetClosedSession } from "./closed-tabs";

// Routes that share one sidebar entry are one page: Ability is programs,
// skills, MCP and plugins; History is chats, projects and memory.
const PAGE_FAMILIES: Record<string, string> = {
  programs: "ability", skills: "ability", mcp: "ability", plugins: "ability",
  chats: "history", projects: "history", memory: "history", history: "history",
};

/** The page a management route belongs to, so a second visit finds its tab. */
export function routePageKey(route: string): string {
  const root = route.split(/[?#]/, 1)[0].split("/").filter(Boolean)[0] ?? "";
  return PAGE_FAMILIES[root] ?? root;
}

export function navigationActions(set: StoreApi<CenterTabsState>["setState"], get: StoreApi<CenterTabsState>["getState"], closedSessionAckTombstones: Set<string>): Pick<CenterTabsState, "recordRouteNavigation" | "canNavigateHistory" | "navigateHistory" | "navigateSessionHistory" | "navigateFileHistory" | "canNavigateFile" | "updateFileNavigationView" | "recordFileNavigation" | "removeSessionFromHistory"> {
  return {
    recordRouteNavigation: pathname => set(s => {
      const active = s.tabs.find(tab => tab.id === s.activeId);
      if (!active) return {};
      const navigationRoute = pathname === "/chat" || pathname.startsWith("/s/") ? undefined : pathname;
      if (active.navigationRoute === navigationRoute) return {};
      // A page that already has a tab is focused there instead of turning the
      // current tab into a second copy of it.
      const key = navigationRoute && routePageKey(navigationRoute);
      const existing = key ? s.tabs.find(tab => tab.id !== active.id && tab.navigationRoute && routePageKey(tab.navigationRoute) === key) : undefined;
      if (existing && navigationRoute) {
        const next = existing.navigationRoute === navigationRoute ? existing : recordTabPage(existing, { ...existing, navigationRoute });
        return commitCenterTabsState(s, { tabs: s.tabs.map(tab => tab.id === existing.id ? next : tab), activeId: existing.id });
      }
      // A split pane only holds chat-area content: a management page opened
      // from a grouped tab gets its own top-level tab beside the group, so the
      // split stays intact and the strip never shows a page as a split member.
      const group = navigationRoute ? findCenterTabGroup(s.groups, active.id) : undefined;
      if (group) {
        const tab: CenterTab = { id: nextNtpId(), kind: "ntp", title: "", navigationRoute };
        const lastMember = Math.max(...group.memberIds.map(id => s.tabs.findIndex(item => item.id === id)));
        const tabs = [...s.tabs.slice(0, lastMember + 1), tab, ...s.tabs.slice(lastMember + 1)];
        return commitCenterTabsState(s, { tabs, activeId: tab.id });
      }
      const next = recordTabPage(active, { ...active, navigationRoute });
      return commitCenterTabsState(s, { tabs: s.tabs.map(tab => tab.id === active.id ? next : tab) });
    }),

    canNavigateHistory: (direction): boolean => navigationTarget(get(), direction) !== null,

    navigateHistory: (direction): void => {
      const target = navigationTarget(get(), direction);
      if (!target) return;
      const existing = target.page.kind === "session" && target.page.sessionId
        ? get().tabs.find(tab => tab.id !== target.owner.id && tab.kind === "session" && tab.sessionId === target.page.sessionId)
        : undefined;
      if (!existing && target.owner.kind === "session" && target.owner.sessionId) closedSessionAckTombstones.add(target.owner.sessionId);
      set(s => {
        if (existing) return commitCenterTabsState(s, { activeId: existing.id });
        const entries = [...target.history.entries];
        entries[target.history.index] = tabPage(target.owner);
        let next = restoreTabPage({ ...target.history, entries }, target.index);
        next = independentTab(next, s.tabs, target.owner.id);
        entries[target.index] = tabPage(next);
        next = { ...next, pageHistory: { entries, index: target.index } };
        return commitCenterTabsState(s, {
          tabs: s.tabs.map(tab => tab.id === target.owner.id ? next : tab), activeId: next.id,
          groups: replaceGroupTabId(s.groups, target.owner.id, next.id),
        });
      });
      if (typeof window !== "undefined" && window.location) {
        const pathname = window.location.pathname;
        const page = existing ?? target.page;
        const path = page.navigationRoute ?? (page.kind === "session" && page.sessionId && !page.draft
          ? `/s/${encodeURIComponent(page.sessionId)}`
          : page.kind === "ntp" || page.kind === "session" || (pathname !== "/chat" && !pathname.startsWith("/s/"))
            ? "/chat" : pathname);
        navigateTabRoute(path);
      }
    },

    navigateSessionHistory: direction => get().navigateHistory(direction),

    navigateFileHistory: direction => get().navigateHistory(direction),

    canNavigateFile: (direction): boolean => get().canNavigateHistory(direction),

    updateFileNavigationView: view => set(s => {
      const active = s.tabs.find(tab => tab.id === s.activeId);
      if (!active?.fileNavigationSnapshot) return {};
      const snapshot = { ...active.fileNavigationSnapshot, expanded: [...view.expanded], scroll: view.scroll && { ...view.scroll } };
      const history = pageHistory(active);
      const next = { ...active, fileNavigationSnapshot: snapshot };
      const entries = history.entries.map((page, index) => index === history.index ? tabPage(next) : page);
      return commitCenterTabsState(s, { tabs: s.tabs.map(tab => tab.id === active.id ? { ...next, pageHistory: { ...history, entries } } : tab) });
    }),

    recordFileNavigation: snapshot => set(s => {
      const active = s.tabs.find(tab => tab.id === s.activeId);
      if (!active || (active.kind !== "file" && !(active.kind === "builtin" && active.page === "files"))) return {};
      const current = active.fileNavigationSnapshot;
      if (current?.projectId === snapshot.projectId && current.path === snapshot.path && current.selectedType === snapshot.selectedType) return {};
      snapshot = { ...snapshot, expanded: [...snapshot.expanded], scroll: snapshot.scroll && { ...snapshot.scroll } };
      let next: CenterTab = snapshot.selectedType === "dir"
        ? { id: active.id, kind: "builtin", title: "", page: "files", fileNavigationSnapshot: snapshot }
        : { ...active, fileNavigationSnapshot: snapshot };
      if (current || next.kind !== active.kind) next = recordTabPage(active, next);
      else if (active.pageHistory) next = {
        ...next, pageHistory: {
          ...active.pageHistory,
          entries: active.pageHistory.entries.map((page, index) => index === active.pageHistory!.index ? tabPage(next) : page)
        }
      };
      return { ...commitCenterTabsState(s, { tabs: s.tabs.map(tab => tab.id === active.id ? next : tab) }), fileNavigationRestore: null };
    }),

    removeSessionFromHistory: (sessionId) => {
      closedSessionAckTombstones.add(sessionId);
      set(s => {
        let activeId = s.activeId;
        let groups = s.groups;
        let closedActive = false;
        const tabs = s.tabs.flatMap(tab => {
          const history = pageHistory(tab);
          const keep = (page: CenterTab) => page.kind !== "session" || page.sessionId !== sessionId;
          if (history.entries.every(keep)) return [tab];
          const entries = history.entries.filter(keep);
          if (!entries.length) {
            if (tab.id === s.activeId) closedActive = true;
            return [];
          }
          const index = Math.max(0, history.entries.slice(0, history.index + 1).filter(keep).length - 1);
          const next = independentTab(restoreTabPage({ entries, index }, index), s.tabs, tab.id);
          next.pageHistory!.entries[index] = tabPage(next);
          if (activeId === tab.id) activeId = next.id;
          groups = replaceGroupTabId(groups, tab.id, next.id);
          return [next];
        });
        // A tab left with nothing to show closes like the strip's ×: focus
        // moves to its neighbour, not to whichever tab comes first.
        if (closedActive && s.activeId) {
          const layout = normalizeCenterTabLayout({ tabIds: tabs.map(tab => tab.id), groups });
          activeId = focusAfterClose(s, s.activeId, tabs, layout.groups);
        }
        return {
          ...commitCenterTabsState(s, { tabs, activeId, groups }),
          closedTabs: forgetClosedSession(s.closedTabs, sessionId),
        };
      });
    }
  };
}
