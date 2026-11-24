import type { CenterTab } from "../../tabs/center-tabs-store";
import type { CenterTabGroup } from "../../tabs/center-tab-groups";
import { listedBrowserResources } from "../../chat/resources/session-resources";

/** Explicit split layouts and user-revealed pages remain in the strip; other session-owned standalone pages live in their session resource panel. */
export function topLevelTabs(tabs: readonly CenterTab[], groups: readonly CenterTabGroup[]) {
  const grouped = new Set(groups.flatMap(group => group.memberIds));
  return tabs.filter(tab => tab.kind !== "web" || grouped.has(tab.id) || tab.webPinned === true
    || (tab.webPinned !== false && (!tab.agentOpened || !tab.agentSessionId)));
}

type CenterTabsRevealStore = {
  tabs: readonly CenterTab[];
  groups: readonly CenterTabGroup[];
  setWebTabPinned: (id: string, pinned: boolean) => void;
  setActive: (id: string) => void;
};

/** Explicit Open in tab: keep the exact existing Page and make it an ordinary current top view. */
export function revealExistingWebTab(tabId: string, store: CenterTabsRevealStore): boolean {
  if (!store.tabs.some(tab => tab.id === tabId)) return false;
  if (!topLevelTabs(store.tabs, store.groups).some(tab => tab.id === tabId)) {
    store.setWebTabPinned(tabId, true);
  }
  store.setActive(tabId);
  return true;
}

/** A conversation's page that is closed everywhere and is not in the strip has
 *  nothing left to show, so its tab goes too. Pages in the strip, on the
 *  canvas, or with no record at all stay. */
export function closedHiddenPageTabs(tabs: readonly CenterTab[], groups: readonly CenterTabGroup[]): string[] {
  const open = new Map<string, boolean>();
  for (const row of listedBrowserResources()) {
    if (!row.tabId || row.kind !== "web") continue;
    open.set(row.tabId, !!open.get(row.tabId) || row.status !== "closed");
  }
  if (![...open.values()].includes(false)) return [];
  const top = new Set(topLevelTabs(tabs, groups).map(tab => tab.id));
  return tabs.filter(tab => tab.kind === "web" && !tab.canvasAnchor && !top.has(tab.id) && open.get(tab.id) === false)
    .map(tab => tab.id);
}

export function groupWebPages(tabs: readonly CenterTab[]) {
  const groups = new Map<string, { key: string; sessionId: string | null; agent: boolean; tabs: CenterTab[] }>();
  for (const tab of tabs) {
    if (tab.kind !== "web") continue;
    const sessionId = tab.agentSessionId || null;
    const key = sessionId ? `session:${sessionId}` : tab.agentOpened ? "agent" : "manual";
    let group = groups.get(key);
    if (!group) {
      group = { key, sessionId, agent: !!tab.agentOpened, tabs: [] };
      groups.set(key, group);
    }
    group.tabs.push(tab);
  }
  return [...groups.values()];
}

/** Session association is independent of who originally opened the Page. */
export function webTabSessionIds(tab: CenterTab): string[] {
  if (tab.kind !== "web") return [];
  const windowId = typeof window === "undefined" ? undefined : window.openprogramDesktop?.windowId;
  return [...new Set([
    ...(tab.agentSessionId ? [tab.agentSessionId] : []),
    ...listedBrowserResources().flatMap(row => {
      const sessionId = row.conversationSessionId || row.scopeSessionId || row.sessionId;
      const sameWindow = !windowId || !row.windowId || row.windowId === windowId;
      return sameWindow && row.tabId === tab.id && row.status !== "closed" && sessionId ? [sessionId] : [];
    }),
  ])];
}

/** Private retained Pages are accessible only to their recorded conversation.
 * Explicit top-level tabs are shared; exclusive operation admission stays server-owned. */
export function agentCanAccessWebTab(
  tabId: string,
  sessionId: string | null | undefined,
  state: { tabs: readonly CenterTab[]; groups: readonly CenterTabGroup[] },
): boolean {
  const tab = state.tabs.find(item => item.id === tabId && item.kind === "web");
  return !!tab && (topLevelTabs(state.tabs, state.groups).some(item => item.id === tabId)
    || (!!sessionId && webTabSessionIds(tab).includes(sessionId)));
}
