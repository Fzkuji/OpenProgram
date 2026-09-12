import type { CenterTab } from "./center-tabs-store";
import { sessionHistory } from "./session-tab-history";

export type TabPage = Omit<CenterTab, "pageHistory">;
export interface TabPageHistory { entries: TabPage[]; index: number }

export function tabPage(tab: CenterTab): TabPage {
  const { pageHistory: _history, ...page } = tab;
  return page;
}

/** Record replacement in the same visible tab, not a newly opened tab. */
export function recordTabPage(from: CenterTab, to: CenterTab): CenterTab {
  const history = from.pageHistory ?? { entries: [tabPage(from)], index: 0 };
  const entries = history.entries.slice(0, history.index + 1);
  entries[history.index] = tabPage(from);
  return { ...to, pageHistory: { entries: [...entries, tabPage(to)], index: entries.length } };
}

export function canNavigateTabPage(tab: CenterTab | undefined, direction: -1 | 1): boolean {
  if (!tab) return false;
  if (tab.kind === "session") {
    const history = sessionHistory(tab);
    const index = history.index + direction;
    if (index >= 0 && index < history.entries.length) return true;
  }
  const history = tab.pageHistory;
  return !!history && history.index + direction >= 0 && history.index + direction < history.entries.length;
}

/** Read only non-recursive snapshots with a matching current identity. */
export function normalizeTabPageHistory(tab: CenterTab): CenterTab {
  const history = tab.pageHistory;
  if (history === undefined) return tab;
  const valid = history && Array.isArray(history.entries) && history.entries.length > 0
    && Number.isInteger(history.index) && history.index >= 0 && history.index < history.entries.length
    && history.entries.every(page => page && typeof page.id === "string" && typeof page.title === "string"
      && ["ntp", "builtin", "session", "file", "web", "application"].includes(page.kind)
      && !("pageHistory" in page))
    && history.entries[history.index].id === tab.id && history.entries[history.index].kind === tab.kind;
  return valid ? tab : tabPage(tab);
}
