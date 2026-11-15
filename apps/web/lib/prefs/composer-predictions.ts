/**
 * composer-predictions — whether the composer suggests the next message
 * after a turn ends (one extra call to the background model per turn).
 * A preference of this browser, in localStorage; on by default.
 */
import { useSyncExternalStore } from "react";

const KEY = "openprogram.composer-predictions";
const EVENT = "openprogram:composer-predictions";

function read(): boolean {
  try {
    return typeof localStorage === "undefined" || localStorage.getItem(KEY) !== "off";
  } catch {
    return true;
  }
}

export function setComposerPredictions(on: boolean): void {
  try { localStorage.setItem(KEY, on ? "on" : "off"); } catch { /* holds for this page only */ }
  if (typeof window !== "undefined") window.dispatchEvent(new Event(EVENT));
}

function subscribe(onChange: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  const onStorage = (event: StorageEvent) => { if (event.key === KEY) onChange(); };
  window.addEventListener(EVENT, onChange);
  window.addEventListener("storage", onStorage);
  return () => { window.removeEventListener(EVENT, onChange); window.removeEventListener("storage", onStorage); };
}

export function useComposerPredictions(): boolean {
  return useSyncExternalStore(subscribe, read, () => false);
}
