import type { CenterTab } from "./center-tabs-store";

export type SessionResource = {
  id: string;
  sessionId: string | null;
  kind: string;
  title: string;
  target: string;
  status: string;
  source: "web" | "file" | "terminal" | "usage" | "process";
  sourceId: string;
  scopeSessionId?: string;
};
export type BackendResource = {
  id: string; session_id: string; execution_id?: string | null;
  kind: string; title: string; target: string; status: string;
  source: "usage" | "process";
};

export function resourceSessionIds(tabs: readonly CenterTab[], current: string | null) {
  return [...new Set([current, ...tabs.flatMap(tab => [
    tab.kind === "session" && !tab.draft ? tab.sessionId : null,
    tab.agentSessionId, tab.diffSessionId,
  ])].filter((id): id is string => !!id))].sort();
}

export function sessionResourceRows(tabs: readonly CenterTab[], backend: readonly SessionResource[]) {
  const views: SessionResource[] = tabs.flatMap(tab => {
    const kind = tab.kind === "web" || tab.kind === "file" ? tab.kind
      : tab.kind === "builtin" && tab.page === "terminal" ? "terminal" : null;
    if (!kind) return [];
    return [{
      id: `tab:${tab.id}`, sessionId: kind === "web" ? tab.agentSessionId || null : tab.diffSessionId || null,
      kind, title: tab.title || tab.url || tab.path || (kind === "terminal" ? "Terminal" : tab.id),
      target: tab.url || tab.path || "", status: "open", source: kind, sourceId: tab.id,
    }];
  });
  // A descendant process can be returned in more than one requested conversation scope.
  const unique = new Map([...views, ...backend].map(row => [row.id, row]));
  return [...unique.values()];
}

export function backendResourceRows(items: readonly BackendResource[], scopeSessionId: string): SessionResource[] {
  return items.map(item => ({
    id: `${item.source}:${item.id}`, sourceId: item.id, source: item.source,
    sessionId: item.session_id, scopeSessionId, kind: item.kind,
    title: item.title, target: item.target, status: item.status,
  }));
}
