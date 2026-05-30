/**
 * user-profile — the local "You" identity (display name + avatar),
 * the counterpart to ``agent-style``'s default-agent profile.
 *
 * Same localStorage + ``useSyncExternalStore`` shape as
 * ``lib/agent-style.ts``: a cached snapshot (stable identity so React
 * doesn't loop), a change event, and a hook. Edited in
 * /settings/general → You; consumed by the user message bubble.
 *
 * Stored per-browser (localStorage) — there's one local user per
 * install today, so this isn't server data.
 */

import type { AvatarConfig } from "@/components/avatar";
import { useSyncExternalStore } from "react";

export interface UserProfilePrefs {
  name: string;
  /** 1–2 char fallback for letter-mode avatars. */
  initial: string;
  /** Letter-mode background colour. */
  color: string;
  /** Avatar config (DiceBear / upload / letter). Optional so profiles
   *  persisted before this field existed still parse. */
  avatar?: AvatarConfig;
}

export const DEFAULT_USER_PROFILE: UserProfilePrefs = {
  name: "You",
  initial: "Y",
  color: "#4f8ef7",
  // Matches the avatar the user bubble showed before this was
  // configurable, so nothing changes until the user customises it.
  avatar: { kind: "dicebear", style: "shapes", seed: "you" },
};

const STORAGE_KEY = "user_profile";
const CHANGE_EVT = "user-profile-change";

let _cached: UserProfilePrefs | null = null;

function _read(): UserProfilePrefs {
  if (typeof window === "undefined") return DEFAULT_USER_PROFILE;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_USER_PROFILE;
    const p = JSON.parse(raw) as Partial<UserProfilePrefs>;
    return {
      name: p.name || DEFAULT_USER_PROFILE.name,
      initial: (p.initial || DEFAULT_USER_PROFILE.initial).slice(0, 2),
      color: p.color || DEFAULT_USER_PROFILE.color,
      avatar: p.avatar ?? DEFAULT_USER_PROFILE.avatar,
    };
  } catch {
    return DEFAULT_USER_PROFILE;
  }
}

export function getUserProfile(): UserProfilePrefs {
  if (_cached === null) _cached = _read();
  return _cached;
}

export function setUserProfile(next: UserProfilePrefs): void {
  if (typeof window === "undefined") return;
  _cached = {
    name: next.name || DEFAULT_USER_PROFILE.name,
    initial: (next.initial || DEFAULT_USER_PROFILE.initial).slice(0, 2),
    color: next.color || DEFAULT_USER_PROFILE.color,
    avatar: next.avatar,
  };
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(_cached));
  window.dispatchEvent(new Event(CHANGE_EVT));
}

export function subscribeUserProfile(fn: () => void): () => void {
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

/** Re-renders whenever the "You" profile changes. */
export function useUserProfile(): UserProfilePrefs {
  return useSyncExternalStore(
    subscribeUserProfile,
    getUserProfile,
    () => DEFAULT_USER_PROFILE,
  );
}
