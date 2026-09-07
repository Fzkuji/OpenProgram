import type { CenterTab } from "./center-tabs-store";
import type { CenterTabGroup } from "./center-tab-groups";

/** Explicit split layouts remain in the strip; session-owned standalone pages live in their session resource panel. */
export function topLevelTabs(tabs: readonly CenterTab[], groups: readonly CenterTabGroup[]) {
  const grouped = new Set(groups.flatMap(group => group.memberIds));
  return tabs.filter(tab => tab.kind !== "web" || !tab.agentOpened || !tab.agentSessionId || tab.webPinned || grouped.has(tab.id));
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
