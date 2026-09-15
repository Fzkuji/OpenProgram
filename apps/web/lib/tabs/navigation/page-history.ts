import type { CenterTab } from "../store/types";
import { sessionHistory } from "./session-history";

/** Settings belong to the application shell, never to an individual tab. */
export function isSettingsRoute(pathname: string | undefined): boolean {
  return !!pathname && /^\/settings(?:[/?#]|$)/.test(pathname);
}

export type TabPage = Omit<CenterTab, "pageHistory" | "sessionHistory">;
export interface TabPageHistory { entries: TabPage[]; index: number }

export function tabPage(tab: CenterTab): TabPage {
  const { pageHistory: _history, sessionHistory: _sessions, ...page } = tab;
  return Object.fromEntries(Object.entries(page).filter(([, value]) => value !== undefined)) as TabPage;
}

/** Migrate legacy session history without consulting any other tab. */
export function pageHistory(tab: CenterTab): TabPageHistory {
  if (tab.pageHistory) return tab.pageHistory;
  const sessions = sessionHistory(tab);
  if (tab.kind === "session" && sessions.entries.length > 1) return {
    entries: sessions.entries.map(entry => ({ ...tabPage(tab), ...entry })), index: sessions.index,
  };
  return { entries: [tabPage(tab)], index: 0 };
}

/** Append a page only to the current tab's own history. */
export function recordTabPage(from: CenterTab, to: CenterTab): CenterTab {
  const history = pageHistory(from);
  const entries = history.entries.slice(0, history.index + 1);
  entries[history.index] = tabPage(from);
  return { ...to, pageHistory: { entries: [...entries, tabPage(to)], index: entries.length } };
}

export function canNavigateTabPage(tab: CenterTab | undefined, direction: -1 | 1): boolean {
  if (!tab || (direction !== -1 && direction !== 1)) return false;
  const history = pageHistory(tab);
  return history.index + direction >= 0 && history.index + direction < history.entries.length;
}

/** Keep the legacy session projection consistent for existing consumers. */
export function restoreTabPage(history: TabPageHistory, index: number): CenterTab {
  const page = history.entries[index];
  if (page.kind !== "session") return { ...page, pageHistory: { ...history, index } };
  const entries = history.entries.filter(item => item.kind === "session").map(item => ({
    sessionId: item.sessionId!, title: item.title, draft: item.draft,
  }));
  const sessionIndex = history.entries.slice(0, index + 1).filter(item => item.kind === "session").length - 1;
  return { ...page, pageHistory: { ...history, index }, sessionHistory: { entries, index: sessionIndex } };
}

/** Read only non-recursive snapshots with a matching current identity. */
export function normalizeTabPageHistory(tab: CenterTab): CenterTab {
  const history = tab.pageHistory;
  if (history === undefined) return removeSettingsVisits(tab);
  const valid = history && Array.isArray(history.entries) && history.entries.length > 0
    && Number.isInteger(history.index) && history.index >= 0 && history.index < history.entries.length
    && history.entries.every(page => page && typeof page.id === "string" && typeof page.title === "string"
      && ["ntp", "builtin", "session", "file", "web", "application"].includes(page.kind)
      && !("pageHistory" in page))
    && history.entries[history.index].id === tab.id && history.entries[history.index].kind === tab.kind;
  return removeSettingsVisits(valid ? tab : tabPage(tab));
}


/** Apply metadata updates to the current page and every retained page. */
export function mapTabPages(tab: CenterTab, update: (page: CenterTab) => CenterTab): CenterTab {
  const next = update(tab);
  if (!tab.pageHistory) return next;
  const entries = tab.pageHistory.entries.map((page, index) => index === tab.pageHistory!.index
    ? tabPage(next) : (() => { const updated = update(page); return updated === page ? page : tabPage(updated); })());
  const changed = next !== tab || entries.some((page, index) => index !== tab.pageHistory!.index && page !== tab.pageHistory!.entries[index]);
  return changed ? { ...next, pageHistory: { ...tab.pageHistory, entries } } : tab;
}


/** Migrate settings overlays recorded by older clients, including transfer payloads. */
function removeSettingsVisits(tab: CenterTab): CenterTab {
  const history = tab.pageHistory;
  const currentIsSettings = isSettingsRoute(tab.navigationRoute);
  if (!currentIsSettings && !history?.entries.some(page => isSettingsRoute(page.navigationRoute))) return tab;
  const { navigationRoute: _route, ...content } = tab;
  if (!history) return content;

  const entries = history.entries.filter(page => !isSettingsRoute(page.navigationRoute));
  let index = history.entries.slice(0, history.index + 1)
    .filter(page => !isSettingsRoute(page.navigationRoute)).length - 1;
  const previous = entries[index];
  // Settings keep the underlying tab identity. Restore its preceding route,
  // but retain metadata updated while settings were open (e.g. draft ACK/title).
  const restorePrevious = currentIsSettings && previous?.id === tab.id && previous.kind === tab.kind;
  const next = currentIsSettings
    ? { ...content, ...(restorePrevious && previous.navigationRoute ? { navigationRoute: previous.navigationRoute } : {}) }
    : tab;
  if (currentIsSettings && !restorePrevious) {
    index += 1;
    entries.splice(index, 0, tabPage(next));
  } else {
    entries[index] = tabPage(next);
  }
  return { ...next, pageHistory: { entries, index } };
}
