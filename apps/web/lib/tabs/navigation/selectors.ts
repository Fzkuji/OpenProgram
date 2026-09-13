import type { CenterTab, CenterTabsState, FileNavigationHistory } from "../store/types";
import { pageHistory } from "./page-history";

/** The selected tab is the only owner consulted by navigation. */
export function navigationTarget(state: CenterTabsState, direction: -1 | 1) {
  if (direction !== -1 && direction !== 1) return null;
  const active = state.tabs.find(tab => tab.id === state.activeId);
  if (!active) return null;
  const history = pageHistory(active);
  const index = history.index + direction;
  return index >= 0 && index < history.entries.length
    ? { owner: active, page: history.entries[index], history, index } : null;
}

export function fileHistoryFor(tab: CenterTab | undefined): FileNavigationHistory {
  if (!tab) return { entries: [], index: -1 };
  const history = pageHistory(tab);
  const entries = history.entries.flatMap(page => page.fileNavigationSnapshot ? [page.fileNavigationSnapshot] : []);
  const index = history.entries.slice(0, history.index + 1).filter(page => page.fileNavigationSnapshot).length - 1;
  return { entries, index };
}
