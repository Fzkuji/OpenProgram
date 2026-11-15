/** Recently closed tabs for Cmd+Shift+T, newest last. Kept in memory only:
 *  a reload starts with an empty stack, like a fresh browser window. */
import { sessionHistory } from "../navigation/session-history";
import type { CenterTab } from "./types";

export const MAX_CLOSED_TABS = 10;

export interface ClosedTabEntry {
  tab: CenterTab;
  /** Position in the tab list when it closed; reopening goes back there. */
  index: number;
}

/** Drafts, new-tab pages, application windows (their instance is gone) and
 *  pages an agent opened are not worth bringing back. */
export function reopenableTab(tab: CenterTab): boolean {
  if (tab.canvasAnchor || tab.agentOpened) return false;
  if (tab.kind === "session") return !!tab.sessionId && !tab.draft;
  return tab.kind === "file" || tab.kind === "web" || tab.kind === "builtin";
}

export function rememberClosedTab(
  stack: readonly ClosedTabEntry[],
  tab: CenterTab,
  index: number,
): ClosedTabEntry[] {
  if (!reopenableTab(tab)) return stack as ClosedTabEntry[];
  // Per-visit state does not come back with the tab.
  const { dirty: _dirty, dagView: _dagView, scrollToLine: _line, highlightLines: _lines, ...kept } = tab;
  return [...stack, { tab: kept, index }].slice(-MAX_CLOSED_TABS);
}

/** Every session a closed tab can navigate to (its back/forward entries too). */
export function closedTabSessionIds(tab: CenterTab): string[] {
  return [tab, ...(tab.pageHistory?.entries ?? [])].flatMap(page => page.kind === "session"
    ? sessionHistory(page).entries.flatMap(entry => entry.sessionId ? [entry.sessionId] : [])
    : []);
}

export function forgetClosedSession(
  stack: readonly ClosedTabEntry[],
  sessionId: string,
): ClosedTabEntry[] {
  const next = stack.filter(entry => entry.tab.sessionId !== sessionId);
  return next.length === stack.length ? stack as ClosedTabEntry[] : next;
}
