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
 * ``lib/agent-style.ts``: a cached snapshot (stable object identity so
 * React doesn't loop), a change event, and a hook.
 */

export type RecentsStatus = "active" | "archived" | "all";
/** How the list is sectioned:
 *  - none    → date buckets when sorted by recency, else a flat list
 *  - state   → Working (a task is running) / Completed
 *  - project → by the conversation's project path (backend-fed) */
export type RecentsGroupBy = "none" | "state" | "project";
export type RecentsSort = "recency" | "title";
/** Time window on the conversation's last activity (created_at /
 *  updated_at). "all" = no window. */
export type RecentsActivity = "all" | "1d" | "7d" | "30d";

export interface RecentsView {
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
}

export const DEFAULT_RECENTS_VIEW: RecentsView = {
  status: "active",
  project: "all",
  environment: "all",
  lastActivity: "all",
  groupBy: "none",
  sort: "recency",
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
      status: p.status || DEFAULT_RECENTS_VIEW.status,
      project: p.project || DEFAULT_RECENTS_VIEW.project,
      environment: p.environment || DEFAULT_RECENTS_VIEW.environment,
      lastActivity: p.lastActivity || DEFAULT_RECENTS_VIEW.lastActivity,
      groupBy: p.groupBy || DEFAULT_RECENTS_VIEW.groupBy,
      sort: p.sort || DEFAULT_RECENTS_VIEW.sort,
    };
  } catch {
    return DEFAULT_RECENTS_VIEW;
  }
}

export function getRecentsView(): RecentsView {
  if (_cached === null) _cached = _read();
  return _cached;
}

/** Merge a partial update into the stored view + notify subscribers. */
export function setRecentsView(patch: Partial<RecentsView>): void {
  if (typeof window === "undefined") return;
  _cached = { ...getRecentsView(), ...patch };
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(_cached));
  window.dispatchEvent(new Event(CHANGE_EVT));
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
