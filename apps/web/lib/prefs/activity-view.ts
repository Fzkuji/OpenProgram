/**
 * activity-view: which sections of the Activity panel this browser keeps
 * folded. A view preference, so it lives in localStorage like the sidebar's
 * recents view; it falls back to the default wherever storage is unavailable.
 */
import { useSyncExternalStore } from "react";

export interface ActivityView {
  /** Keys of the overview sections folded to their header. */
  collapsed: string[];
  /** Agents whose helpers are folded under their row, one key per
   *  conversation and agent; the most recent MAX_FOLDED_AGENTS are kept. */
  foldedAgents: string[];
}

export const DEFAULT_ACTIVITY_VIEW: ActivityView = { collapsed: [], foldedAgents: [] };
const MAX_FOLDED_AGENTS = 200;

const KEY = "openprogram.activity-view";
const EVENT = "openprogram:activity-view";
let cached: ActivityView | null = null;

function read(): ActivityView {
  if (cached) return cached;
  let saved: Partial<ActivityView> = {};
  try {
    const raw = typeof localStorage !== "undefined" ? localStorage.getItem(KEY) : null;
    saved = raw ? JSON.parse(raw) as Partial<ActivityView> : {};
  } catch { saved = {}; }
  cached = {
    collapsed: Array.isArray(saved.collapsed) ? saved.collapsed.filter(key => typeof key === "string") : DEFAULT_ACTIVITY_VIEW.collapsed,
    foldedAgents: Array.isArray(saved.foldedAgents) ? saved.foldedAgents.filter(key => typeof key === "string") : DEFAULT_ACTIVITY_VIEW.foldedAgents,
  };
  return cached;
}

export function setActivityView(patch: Partial<ActivityView>): void {
  cached = { ...read(), ...patch };
  try { localStorage.setItem(KEY, JSON.stringify(cached)); } catch { /* the choice still holds for this page */ }
  if (typeof window !== "undefined") window.dispatchEvent(new Event(EVENT));
}

/** Fold or unfold one overview section. */
export function toggleActivitySection(key: string): void {
  const { collapsed } = read();
  setActivityView({ collapsed: collapsed.includes(key) ? collapsed.filter(item => item !== key) : [...collapsed, key] });
}

/** Fold or unfold the helpers under one agent of one conversation. */
export function toggleAgentHelpers(key: string): void {
  const { foldedAgents } = read();
  setActivityView({ foldedAgents: foldedAgents.includes(key)
    ? foldedAgents.filter(item => item !== key) : [...foldedAgents, key].slice(-MAX_FOLDED_AGENTS) });
}

function subscribe(onChange: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  const onStorage = (event: StorageEvent) => { if (event.key === KEY) { cached = null; onChange(); } };
  window.addEventListener(EVENT, onChange);
  window.addEventListener("storage", onStorage);
  return () => { window.removeEventListener(EVENT, onChange); window.removeEventListener("storage", onStorage); };
}

export function useActivityView(): ActivityView {
  return useSyncExternalStore(subscribe, read, () => DEFAULT_ACTIVITY_VIEW);
}
