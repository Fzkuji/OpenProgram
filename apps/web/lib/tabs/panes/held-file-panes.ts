"use client";

/** File panes kept mounted after their tab has closed.
 *
 *  A document's editor lives in its pane, and saving it can take seconds
 *  (an Office deck is exported whole). The tab and its place in the layout
 *  go in the click's frame; the pane stays mounted, out of sight, until the
 *  document has been saved and released. A pane closed out of a split first
 *  stands where it stood (`frame`) for the fold that takes it away. */
import { useSyncExternalStore } from "react";
import type { CanvasFrame } from "@/lib/tabs/canvas/canvas-transition";

export interface HeldFilePane {
  tab: { id: string; kind: string; projectId?: string; path?: string; readOnly?: boolean; fileSessionId?: string; previewOwnerSessionId?: string };
  /** Where the pane stands while it folds away; null once out of sight. */
  frame: CanvasFrame | null;
}

let held: readonly HeldFilePane[] = [];
const listeners = new Set<() => void>();
function commit(next: readonly HeldFilePane[]) { held = next; for (const listener of listeners) listener(); }

export function holdFilePane(tab: HeldFilePane["tab"], frame: CanvasFrame | null): void {
  commit([...held.filter(entry => entry.tab.id !== tab.id), { tab, frame }]);
}
/** The fold is over: the pane goes out of sight and stays mounted. */
export function hideHeldFilePane(tabId: string): void {
  if (held.some(entry => entry.tab.id === tabId && entry.frame)) commit(held.map(entry => entry.tab.id === tabId ? { ...entry, frame: null } : entry));
}
export function releaseFilePane(tabId: string): void {
  if (held.some(entry => entry.tab.id === tabId)) commit(held.filter(entry => entry.tab.id !== tabId));
}
export function heldFilePanes(): readonly HeldFilePane[] { return held; }
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
const none: readonly HeldFilePane[] = [];
export function useHeldFilePanes(): readonly HeldFilePane[] {
  return useSyncExternalStore(subscribe, heldFilePanes, () => none);
}

if (typeof window !== "undefined") {
  window.addEventListener("op-file-pane-folded", (event) => {
    const tabId = (event as CustomEvent<{ tabId?: string }>).detail?.tabId;
    if (tabId) hideHeldFilePane(tabId);
  });
}
