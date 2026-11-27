import type { ProjectSort } from "../projects/project-groups";
/**
 * recents-view — per-browser view preferences for the sidebar Recents
 * list (which Status to show, how to sort, whether to group).
 *
 * These are *view* preferences, not conversation data — they describe
 * how THIS browser displays the list, so they live in localStorage,
 * not on the server. (The conversation flags they act on — pinned /
 * archived / group — are server data in meta.json.)
 *
 * Same localStorage + ``useSyncExternalStore`` shape as
 * ``lib/prefs/user-profile.ts``: a cached snapshot (stable object identity so
 * React doesn't loop), a change event, and a hook.
 */

export type RecentsStatus = "active" | "archived" | "all";
/** How the list is sectioned (UI labels in parens):
 *  - none    → date buckets — "Date" (the default)
 *  - state   → Working (a task is running) / Completed — "State"
 *  - project → by the conversation's project path (backend-fed) — "Project"
 *  - flat    → one flat run, no headers — "None"
 *  ("none" keeps its value for back-compat with saved prefs; its label is
 *  "Date". "flat" is the true no-grouping option, shown last.) */
export type RecentsGroupBy = "none" | "state" | "project" | "flat";
/** Order of rows: recency (last activity), created (creation time),
 *  title (alphabetical). recency/created both key off created_at until
 *  the backend tracks a separate last-activity timestamp. */
export type RecentsSort = "recency" | "created" | "title";
/** Time window on the conversation's last activity (created_at /
 *  updated_at). "all" = no window. */
export type RecentsActivity = "all" | "1d" | "7d" | "30d";

export interface RecentsView {
  projectOrder: string[];
  projectSort: ProjectSort;
  pinnedProjects: string[];
  /** The default project (where a chat with no project is saved) is
   *  pinned until the person unpins it, so the list shows from the start
   *  where such chats go. */
  defaultUnpinned: boolean;
  projectSectionNames: string[];
  projectSections: Record<string, string>;
  sortDirection: "asc" | "desc";
  status: RecentsStatus;
  /** Project filter. ``"all"`` = no filter. Stored as a project id /
   *  name; the backend that introduces projects fills the option list
   *  + applies the filter — the UI is wired and ready. */
  project: string;
  /** Environment filter. Same "wired, backend-later" shape as project. */
  environment: string;
  lastActivity: RecentsActivity;
  groupBy: RecentsGroupBy;
  sort: RecentsSort;
  /** The list shows Activity (the bell): conversations from the last 7
   *  days that run, wait for the user, failed or have an unread result,
   *  in place of the project or date list. */
  activity: boolean;
}

export const DEFAULT_RECENTS_VIEW: RecentsView = {
  projectOrder: [],
  projectSort: "recency",
  pinnedProjects: [],
  defaultUnpinned: false,
  projectSectionNames: [],
  projectSections: {},
  sortDirection: "desc",
  status: "active",
  project: "all",
  environment: "all",
  lastActivity: "all",
  groupBy: "none",
  sort: "recency",
  activity: false,
};

const STORAGE_KEY = "recents_view";
const CHANGE_EVT = "recents-view-change";

let _cached: RecentsView | null = null;

function _read(): RecentsView {
  if (typeof window === "undefined") return DEFAULT_RECENTS_VIEW;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_RECENTS_VIEW;
    const p = JSON.parse(raw) as Partial<RecentsView>;
    return {
      projectOrder: Array.isArray(p.projectOrder) && p.projectOrder.every((id) => typeof id === "string") ? [...new Set(p.projectOrder)] : [],
      projectSort: ["recency", "oldest", "name", "manual"].includes(p.projectSort ?? "") ? p.projectSort! : (Array.isArray(p.projectOrder) && p.projectOrder.length ? "manual" : "recency"),
      pinnedProjects: Array.isArray(p.pinnedProjects) ? [...new Set(p.pinnedProjects.filter((id) => typeof id === "string"))] : [],
      defaultUnpinned: p.defaultUnpinned === true,
      projectSectionNames: Array.isArray(p.projectSectionNames) ? [...new Set(p.projectSectionNames.filter((name) => typeof name === "string" && name.trim() && name !== "__pinned__"))] : [],
      projectSections: p.projectSections && typeof p.projectSections === "object" && !Array.isArray(p.projectSections) ? Object.fromEntries(Object.entries(p.projectSections).filter(([, value]) => typeof value === "string")) : {},
      sortDirection: p.sortDirection === "asc" || p.sortDirection === "desc" ? p.sortDirection : p.sort === "title" ? "asc" : "desc",
      status: p.status || DEFAULT_RECENTS_VIEW.status,
      project: p.project || DEFAULT_RECENTS_VIEW.project,
      environment: p.environment || DEFAULT_RECENTS_VIEW.environment,
      lastActivity: p.lastActivity || DEFAULT_RECENTS_VIEW.lastActivity,
      groupBy: p.groupBy || DEFAULT_RECENTS_VIEW.groupBy,
      sort: p.sort || DEFAULT_RECENTS_VIEW.sort,
      activity: p.activity === true,
    };
  } catch {
    return DEFAULT_RECENTS_VIEW;
  }
}

/** The pinned projects' ids, the default project's among them unless it
 *  was unpinned. */
export function pinnedProjectIds(view: RecentsView, defaultId: string | null | undefined): string[] {
  const ids = view.pinnedProjects.filter((id) => id !== defaultId);
  return defaultId && !view.defaultUnpinned ? [defaultId, ...ids] : ids;
}

/** Pins or unpins a project; the default project's choice is kept apart. */
export function setProjectPinned(id: string, pinned: boolean, isDefault: boolean): void {
  const view = getRecentsView();
  const rest = view.pinnedProjects.filter((other) => other !== id);
  setRecentsView(isDefault
    ? { pinnedProjects: rest, defaultUnpinned: !pinned }
    : { pinnedProjects: pinned ? [...rest, id] : rest });
}

export function getRecentsView(): RecentsView {
  if (_cached === null) _cached = _read();
  return _cached;
}

/** Merge a partial update into the stored view + notify subscribers. */
export function setRecentsView(patch: Partial<RecentsView>): void {
  if (typeof window === "undefined") return;
  _cached = { ...getRecentsView(), ...patch };
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(_cached));
  } finally {
    window.dispatchEvent(new Event(CHANGE_EVT));
  }
}

/** The bell and ⌘⌥U: switch the list between Activity and the usual list. */
export function toggleActivityView(): void {
  setRecentsView({ activity: !getRecentsView().activity });
}

export function subscribeRecentsView(fn: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  const onChange = () => fn();
  const onStorage = (e: StorageEvent) => {
    if (e.key === STORAGE_KEY) {
      _cached = null;
      fn();
    }
  };
  window.addEventListener(CHANGE_EVT, onChange);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(CHANGE_EVT, onChange);
    window.removeEventListener("storage", onStorage);
  };
}

import { useSyncExternalStore } from "react";

/** Re-renders whenever the Recents view prefs change. */
export function useRecentsView(): RecentsView {
  return useSyncExternalStore(
    subscribeRecentsView,
    getRecentsView,
    () => DEFAULT_RECENTS_VIEW,
  );
}
