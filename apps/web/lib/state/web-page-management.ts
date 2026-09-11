import type { CenterTab } from "./center-tabs-store";
import type { CenterTabGroup } from "./center-tab-groups";

/** Explicit split layouts and user-revealed pages remain in the strip; other session-owned standalone pages live in their session resource panel. */
export function topLevelTabs(tabs: readonly CenterTab[], groups: readonly CenterTabGroup[]) {
  const grouped = new Set(groups.flatMap(group => group.memberIds));
  return tabs.filter(tab => tab.kind !== "web" || !tab.agentOpened || !tab.agentSessionId || tab.webPinned || grouped.has(tab.id));
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

/** Private retained Pages are accessible only to their recorded conversation.
 * Explicit top-level tabs are shared; exclusive operation admission stays server-owned. */
export function agentCanAccessWebTab(
  tabId: string,
  sessionId: string | null | undefined,
  state: { tabs: readonly CenterTab[]; groups: readonly CenterTabGroup[] },
): boolean {
  const tab = state.tabs.find(item => item.id === tabId && item.kind === "web");
  return !!tab && (topLevelTabs(state.tabs, state.groups).some(item => item.id === tabId)
    || (!!sessionId && tab.agentSessionId === sessionId));
}
