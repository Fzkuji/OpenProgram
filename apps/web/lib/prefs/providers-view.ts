/**
 * providers-view: whether Settings → Model services lists providers as cards
 * or as compact rows. A view preference, so it lives in localStorage like
 * the sidebar's recents view and the Activity panel's folds; cards wherever
 * storage is unavailable.
 */
import { useSyncExternalStore } from "react";

export type ProvidersView = "cards" | "rows";

const KEY = "openprogram.providers-view";
const EVENT = "openprogram:providers-view";

/** The choice when storage refused it: it still holds for this page. */
let unsaved: ProvidersView | null = null;

function read(): ProvidersView {
  if (unsaved) return unsaved;
  try {
    return typeof localStorage !== "undefined" && localStorage.getItem(KEY) === "rows" ? "rows" : "cards";
  } catch {
    return "cards";
  }
}

export function setProvidersView(view: ProvidersView): void {
  try { localStorage.setItem(KEY, view); unsaved = null; } catch { unsaved = view; }
  if (typeof window !== "undefined") window.dispatchEvent(new Event(EVENT));
}

function subscribe(onChange: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  const onStorage = (event: StorageEvent) => { if (event.key === KEY) onChange(); };
  window.addEventListener(EVENT, onChange);
  window.addEventListener("storage", onStorage);
  return () => { window.removeEventListener(EVENT, onChange); window.removeEventListener("storage", onStorage); };
}

export function useProvidersView(): ProvidersView {
  return useSyncExternalStore(subscribe, read, () => "cards");
}
