/**
 * The Tasks page's two tabs: Inbox (what always-on Agents and scheduled
 * tasks left for the user) and Scheduled (the tasks themselves). Each tab
 * draws its own header through `TasksHeader`, so the title and the two tabs
 * stay put while the toolbar follows the tab.
 */
import type { ReactNode } from "react";
import type { ManageAction } from "@/components/ui/manage-page";

export type TasksTab = "inbox" | "scheduled";

/** What a tab adds to the shared header: its toolbar and actions. */
export type TasksHeader = (parts: { toolbar?: ReactNode; actions?: ManageAction[] }) => ReactNode;

export const TASKS_TAB_STORAGE_KEY = "openprogram.tasks.tab";

export function coerceTasksTab(value: unknown): TasksTab | null {
  return value === "inbox" || value === "scheduled" ? value : null;
}

/**
 * The tab the page opens on. A link that names a tab (`/tasks?tab=…`, and
 * the old /pending and /scheduler routes) wins; otherwise an inbox with
 * something in it does, since that is waiting for the user; otherwise the
 * tab the user last chose, and Scheduled the first time.
 */
export function openingTab(asked: TasksTab | null, inboxItems: number, remembered: TasksTab | null): TasksTab {
  if (asked) return asked;
  if (inboxItems > 0) return "inbox";
  return remembered ?? "scheduled";
}

export function readRememberedTab(): TasksTab | null {
  try {
    return coerceTasksTab(window.localStorage.getItem(TASKS_TAB_STORAGE_KEY));
  } catch {
    return null;
  }
}

export function rememberTab(tab: TasksTab): void {
  try {
    window.localStorage.setItem(TASKS_TAB_STORAGE_KEY, tab);
  } catch {
    // Storage may be blocked; the choice then lasts for this visit only.
  }
}
