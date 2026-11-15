/**
 * launchpad-store — the saved Launchpad arrangement (page order and
 * folders) for this browser.
 *
 * It is a view arrangement, like the sidebar Recents order, so it lives in
 * localStorage; which applications exist stays server data. Same
 * localStorage + ``useSyncExternalStore`` shape as ``lib/prefs/user-profile.ts``,
 * with every open new-tab page following a change made in another.
 */

import { useSyncExternalStore } from "react";
import { EMPTY_LAYOUT, parseLayout, type LaunchpadLayout } from "./launchpad-layout";

const STORAGE_KEY = "launchpad_layout";
const CHANGE_EVT = "launchpad-layout-change";

let _cached: LaunchpadLayout | null = null;

function _read(): LaunchpadLayout {
  if (typeof window === "undefined") return EMPTY_LAYOUT;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? parseLayout(JSON.parse(raw)) : EMPTY_LAYOUT;
  } catch {
    return EMPTY_LAYOUT;
  }
}

export function getLaunchpadLayout(): LaunchpadLayout {
  if (_cached === null) _cached = _read();
  return _cached;
}

export function setLaunchpadLayout(next: LaunchpadLayout): void {
  if (typeof window === "undefined") return;
  _cached = next;
  try { window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next)); } catch { /* holds for this page only */ }
  window.dispatchEvent(new Event(CHANGE_EVT));
}

function subscribe(fn: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  const onStorage = (e: StorageEvent) => {
    if (e.key === STORAGE_KEY) { _cached = null; fn(); }
  };
  window.addEventListener(CHANGE_EVT, fn);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(CHANGE_EVT, fn);
    window.removeEventListener("storage", onStorage);
  };
}

export function useLaunchpadLayout(): LaunchpadLayout {
  return useSyncExternalStore(subscribe, getLaunchpadLayout, () => EMPTY_LAYOUT);
}
