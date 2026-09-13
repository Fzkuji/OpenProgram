import type { CenterTabsState } from "./center-tabs-store";
import { canNavigateTabPage } from "./tab-page-history";
import { topLevelTabs } from "../browser/web-page-management";

export interface WindowNavigationHistory { entries: string[]; index: number }
type Layout = Pick<CenterTabsState, "tabs" | "groups" | "activeId">;

/** Track visible tab visits without retaining closed tabs or page contents. */
export function recordWindowNavigation(
  state: Layout & { windowNavigationHistory: WindowNavigationHistory },
  next: Layout,
  record = true,
): WindowNavigationHistory {
  const visible = new Set(topLevelTabs(next.tabs, next.groups).map(tab => tab.id));
  const activeId = next.activeId;
  if (!activeId || !visible.has(activeId)) return { entries: [], index: -1 };
  const old = state.windowNavigationHistory;
  const history = old?.entries[old.index] === state.activeId
    ? old : { entries: state.activeId ? [state.activeId] : [], index: state.activeId ? 0 : -1 };
  let entries = [...history.entries];
  let index = history.index;
  const previousTab = state.tabs.find(tab => tab.id === state.activeId);
  const nextTab = next.tabs.find(tab => tab.id === activeId);
  if (record && activeId === state.activeId && previousTab?.sessionId !== nextTab?.sessionId) {
    entries = entries.slice(0, index + 1);
  }
  // Replacement and local history navigation are one visit, not a tab switch.
  if (!record || (state.activeId && !visible.has(state.activeId))) {
    if (index >= 0) entries[index] = activeId;
    else { entries = [activeId]; index = 0; }
  } else if (activeId !== state.activeId || index < 0) {
    entries = [...entries.slice(0, index + 1), activeId];
    index = entries.length - 1;
  }
  const kept: string[] = [];
  let cursor = -1;
  entries.forEach((id, i) => {
    if (visible.has(id) && kept.at(-1) !== id) kept.push(id);
    if (i <= index) cursor = kept.length - 1;
  });
  const trim = Math.max(0, kept.length - 100);
  return { entries: kept.slice(trim), index: Math.max(0, cursor - trim) };
}

export function navigationTarget(state: CenterTabsState, direction: -1 | 1):
  { kind: "page" | "file" } | { kind: "window"; index: number; tabId: string } | null {
  if (direction !== -1 && direction !== 1) return null;
  const active = state.tabs.find(tab => tab.id === state.activeId);
  if (!active) return null;
  if (canNavigateTabPage(active, direction)) return { kind: "page" };
  if (active.kind === "file" || (active.kind === "builtin" && active.page === "files")) {
    const history = state.fileNavigationHistory;
    const index = history.index + direction;
    if (index >= 0 && index < history.entries.length) return { kind: "file" };
  }
  const history = state.windowNavigationHistory;
  if (!history || history.entries[history.index] !== active.id) return null;
  const visible = new Set(topLevelTabs(state.tabs, state.groups).map(tab => tab.id));
  for (let index = history.index + direction; index >= 0 && index < history.entries.length; index += direction) {
    const tabId = history.entries[index];
    if (tabId !== active.id && visible.has(tabId)) return { kind: "window", index, tabId };
  }
  return null;
}
