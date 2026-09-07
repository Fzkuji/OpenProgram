import type { CenterTab } from "./center-tabs-store";

export type SessionResource = {
  id: string;
  sessionId: string | null;
  kind: string;
  title: string;
  target: string;
  status: string;
  source: "web" | "usage";
  sourceId: string;
  scopeSessionId?: string;
};
export type BackendResource = {
  id: string; session_id: string; execution_id?: string | null;
  kind: string; title: string; target: string; status: string;
  source: "usage";
};

export function resourceSessionId(tab: CenterTab | undefined): string | null {
  if (tab?.kind === "session") return tab.draft ? null : tab.sessionId || null;
  if (tab?.kind === "web") return tab.agentSessionId || null;
  if (tab?.kind === "file") return tab.diffSessionId || null;
  return null;
}

export function sessionResourceRows(tabs: readonly CenterTab[], backend: readonly SessionResource[], sessionId: string | null) {
  if (!sessionId) return [];
  const views: SessionResource[] = tabs.flatMap(tab => {
    if (tab.kind !== "web") return [];
    return [{
      id: `tab:${tab.id}`, sessionId: tab.agentSessionId || null,
      kind: "web", title: tab.title || tab.url || tab.id,
      target: tab.url || "", status: "open", source: "web", sourceId: tab.id,
    }];
  });
  // Authorization scope does not change the actual session owner.
  const unique = new Map([...views, ...backend].map(row => [row.id, row]));
  return [...unique.values()].filter(row => row.sessionId === sessionId);
}

export function backendResourceRows(items: readonly BackendResource[], scopeSessionId: string): SessionResource[] {
  return items.filter(item => item.source === "usage").map(item => ({
    id: `${item.source}:${item.id}`, sourceId: item.id, source: item.source,
    sessionId: item.session_id, scopeSessionId, kind: item.kind,
    title: item.title, target: item.target, status: item.status,
  }));
}
