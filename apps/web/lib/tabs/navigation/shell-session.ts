import { findCenterTabGroup, resolveCenterTabPanes } from "../center-tab-groups.ts";
import type { CenterTabsState } from "../store/types";

type TabLayout = Pick<CenterTabsState, "tabs" | "groups" | "activeId">;

/** The tab the singleton chat shell shows, or null while it is hidden. */
export function shellTabId(s: TabLayout): string | null {
  const group = s.activeId ? findCenterTabGroup(s.groups, s.activeId) : undefined;
  const pane = resolveCenterTabPanes(group, s.tabs, s.activeId).find((p) => p.kind === "session");
  return pane?.kind === "session" ? pane.activeTabId : null;
}

/** The session the chat shell must show the moment a split hands it back,
 *  or null when this change does not bring the shell back from a split.
 *  While split, the shell sits hidden on whichever session was focused
 *  last; the route only reaches it a few renders later. */
export function sessionForShellReturn(prev: TabLayout, next: TabLayout): string | null {
  if (!prev.activeId || !findCenterTabGroup(prev.groups, prev.activeId) || shellTabId(prev)) return null;
  const id = shellTabId(next);
  const tab = id ? next.tabs.find((candidate) => candidate.id === id) : undefined;
  return tab?.kind === "session" && !tab.draft && tab.sessionId ? tab.sessionId : null;
}

/** Conversations on screen right now: the active tab's, or every session
 *  pane of the split it belongs to. Drafts have no conversation yet. */
export function visibleSessionIds(s: TabLayout): string[] {
  const group = s.activeId ? findCenterTabGroup(s.groups, s.activeId) : undefined;
  const ids = group ? group.visibleIds : s.activeId ? [s.activeId] : [];
  return ids.flatMap((id) => {
    const tab = s.tabs.find((candidate) => candidate.id === id);
    return tab?.kind === "session" && !tab.draft && tab.sessionId && !tab.navigationRoute
      ? [tab.sessionId] : [];
  });
}
