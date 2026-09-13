import type { CenterTab, CenterTabsState, FileNavigationSnapshot } from "./center-tabs-store";
import { canNavigateTabPage, tabPage } from "./tab-page-history";
import { sessionHistory, withSessionHistory } from "./session-tab-history";
import { topLevelTabs } from "../browser/web-page-management";

export interface WindowVisit {
  tabId: string;
  sessionId?: string;
  route?: string;
  file?: FileNavigationSnapshot;
}
export interface WindowNavigationHistory { entries: WindowVisit[]; index: number }
type Layout = Pick<CenterTabsState, "tabs" | "groups" | "activeId"> & { navigationRoute?: string };

export function activeVisit(state: Layout): WindowVisit | null {
  const tab = state.tabs.find(tab => tab.id === state.activeId);
  return tab ? { tabId: tab.id, route: state.navigationRoute, sessionId: tab.kind === "session" ? tab.sessionId : undefined } : null;
}
function samePage(a: WindowVisit | undefined | null, b: WindowVisit | undefined | null): boolean {
  return !!a && !!b && a.tabId === b.tabId && a.sessionId === b.sessionId && a.route === b.route;
}
function sameVisit(a: WindowVisit | undefined, b: WindowVisit): boolean {
  return samePage(a, b) && a?.file?.projectId === b.file?.projectId
    && a?.file?.path === b.file?.path && a?.file?.selectedType === b.file?.selectedType;
}

/** Resolve retained page/session identities through their current visible owner. */
export function resolveVisit(state: Layout, visit: WindowVisit): { owner: CenterTab; page: CenterTab } | null {
  const visible = topLevelTabs(state.tabs, state.groups);
  for (const owner of [...visible.filter(tab => tab.id === visit.tabId), ...visible.filter(tab => tab.id !== visit.tabId)]) {
    const source = owner.id === visit.tabId ? owner : owner.pageHistory?.entries.find(page => page.id === visit.tabId);
    if (!source) continue;
    let page: CenterTab = source;
    if (visit.sessionId) {
      const history = sessionHistory(source);
      const index = history.entries.findIndex(entry => entry.sessionId === visit.sessionId);
      if (source.kind !== "session" || index < 0) continue;
      page = index === history.index ? source : withSessionHistory(source, { ...history, index });
    }
    return { owner, page };
  }
  return null;
}

/** Record actual content changes, retaining the initial page across replacement. */
export function recordWindowNavigation(
  state: Layout & { windowNavigationHistory: WindowNavigationHistory }, next: Layout, record = true,
): WindowNavigationHistory {
  const current = activeVisit(next);
  if (!current || !resolveVisit(next, current)) return { entries: [], index: -1 };
  const before = activeVisit(state);
  const old = state.windowNavigationHistory;
  const history = samePage(old?.entries[old.index], before)
    ? old : { entries: before ? [before] : [], index: before ? 0 : -1 };
  let entries = [...history.entries];
  let index = history.index;
  if (!samePage(before, current)) {
    if (record && (!before || resolveVisit(next, before))) {
      entries = [...entries.slice(0, index + 1), current];
      index = entries.length - 1;
    } else if (index >= 0) entries[index] = current;
    else { entries = [current]; index = 0; }
  } else if (index < 0) { entries = [current]; index = 0; }
  const kept: WindowVisit[] = [];
  let cursor = -1;
  entries.forEach((entry, i) => {
    if (resolveVisit(next, entry) && !sameVisit(kept.at(-1), entry)) kept.push(entry);
    if (i <= index) cursor = kept.length - 1;
  });
  const trim = Math.max(0, kept.length - 100);
  return { entries: kept.slice(trim), index: Math.max(0, cursor - trim) };
}

export function recordFileVisit(state: CenterTabsState, snapshot: FileNavigationSnapshot): WindowNavigationHistory {
  const current = activeVisit(state);
  const history = state.windowNavigationHistory;
  if (!current) return history;
  const next = { ...current, file: snapshot };
  const previous = history.entries[history.index];
  if (sameVisit(previous, next)) return history;
  // Opening a file first activates its tab, then reports its location.
  const entries = history.entries.slice(0, history.index + 1);
  if (samePage(previous, next) && !previous?.file) entries[history.index] = next;
  else entries.push(next);
  const bounded = entries.slice(-100);
  return { entries: bounded, index: bounded.length - 1 };
}

export function navigationTarget(state: CenterTabsState, direction: -1 | 1):
  { kind: "page" | "file" } | { kind: "window"; index: number; visit: WindowVisit; owner: CenterTab; page: CenterTab } | null {
  if (direction !== -1 && direction !== 1) return null;
  const active = state.tabs.find(tab => tab.id === state.activeId);
  if (!active) return null;
  const history = state.windowNavigationHistory;
  if (history && samePage(history.entries[history.index], activeVisit(state))) {
    for (let index = history.index + direction; index >= 0 && index < history.entries.length; index += direction) {
      const visit = history.entries[index];
      const resolved = resolveVisit(state, visit);
      if (resolved) return { kind: "window", index, visit, ...resolved };
    }
    // A populated timeline owns both endpoints; never fall into older local history.
    if (history.entries.length > 1) return null;
  }
  if (canNavigateTabPage(active, direction)) return { kind: "page" };
  if (active.kind === "file" || (active.kind === "builtin" && active.page === "files")) {
    const local = state.fileNavigationHistory;
    const index = local.index + direction;
    if (index >= 0 && index < local.entries.length) return { kind: "file" };
  }
  return null;
}

export function restoreVisitPage(owner: CenterTab, page: CenterTab): CenterTab {
  if (!owner.pageHistory) return page;
  const entries = [...owner.pageHistory.entries];
  entries[owner.pageHistory.index] = tabPage(owner);
  const index = entries.findIndex(entry => entry.id === page.id);
  return index < 0 ? page : { ...page, pageHistory: { entries, index } };
}
