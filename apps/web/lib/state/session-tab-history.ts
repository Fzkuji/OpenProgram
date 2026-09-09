import type { CenterTab } from "./center-tabs-store";

export type SessionHistoryEntry = Pick<CenterTab, "sessionId" | "title" | "draft">;
export interface SessionTabHistory {
  entries: SessionHistoryEntry[];
  index: number;
}

export function sessionHistory(tab: CenterTab): SessionTabHistory {
  return tab.sessionHistory ?? {
    entries: [{ sessionId: tab.sessionId, title: tab.title, draft: tab.draft }],
    index: 0,
  };
}

/** Invalid persisted histories fall back to the current session, never a
 * partially filtered list whose cursor could select a different session. */
export function normalizeSessionHistory(tab: CenterTab): CenterTab {
  if (tab.kind !== "session" || tab.sessionHistory === undefined) return tab;
  const history = tab.sessionHistory;
  if (!history || typeof history !== "object" || !Array.isArray(history.entries) || !history.entries.length
    || !Number.isInteger(history.index) || history.index < 0
    || history.index >= history.entries.length
    || history.entries.some(entry => !entry || typeof entry.sessionId !== "string"
      || !entry.sessionId || typeof entry.title !== "string"
      || (entry.draft !== undefined && typeof entry.draft !== "boolean"))
    || history.entries[history.index].sessionId !== tab.sessionId
    || history.entries[history.index].title !== tab.title
    || !!history.entries[history.index].draft !== !!tab.draft) {
    const { sessionHistory: _drop, ...rest } = tab;
    return rest;
  }
  return tab;
}

export function withSessionHistory(tab: CenterTab, history: SessionTabHistory): CenterTab {
  return { ...tab, ...history.entries[history.index], draft: !!history.entries[history.index].draft,
    dagView: false, sessionHistory: history };
}
