/**
 * agent-style — deterministic colour + initial for an agent_id, with
 * a localStorage-backed override for the default profile (the one
 * shown when agent_id is missing or "main").
 *
 * The default-profile knobs (display name, avatar initial, avatar
 * colour) are configured in /settings/general → Agent. Named agents
 * (research_agent, gui_agent, etc.) still hash deterministically so
 * multi-agent chats stay visually consistent across reloads.
 */

// Kept in sync with web/components/right-sidebar/branches-panel.tsx
// LANE_COLORS — change both together so the chat avatar matches the
// branch dot in the right rail.
const PALETTE = [
  "#4f8ef7", "#5aad4e", "#d4843a", "#9d6fe0", "#e0445a", "#2db3d5",
  "#e0b020", "#35b89a", "#e066b3", "#6b8dd6", "#8fbf3f", "#d9694f",
  "#52c4c4", "#b08be0", "#c79a4a", "#e08a3a", "#6fae6f", "#d05fa0",
];

// FNV-1a 32-bit. Cheap, stable, no deps. We just want N-bucketed
// hashing of strings — anything sub-millisecond is fine.
function _hash(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0;
  }
  return h >>> 0;
}

/* ---- Default-profile overrides --------------------------------- */

// AgentAvatarConfig lives in the avatar feature module — see
// ``components/avatar/types.ts``. Re-export it from here so existing
// importers of ``lib/agent-style`` keep compiling, but the canonical
// definition stays next to the rest of the avatar code.
import type { AgentAvatarConfig } from "@/components/avatar/types";
export type { AgentAvatarConfig };

export interface AgentProfilePrefs {
  name: string;
  initial: string;
  color: string;
  /** Avatar config. Optional so profiles persisted before this field
   *  existed still parse cleanly — callers fall back to a DiceBear
   *  ``shapes`` avatar seeded by ``name``. */
  avatar?: AgentAvatarConfig;
}

export const DEFAULT_AGENT_PROFILE: AgentProfilePrefs = {
  name: "Agent",
  initial: "A",
  color: "#35b89a",
};

const STORAGE_KEY = "agent_profile";
const CHANGE_EVT = "agent-profile-change";

// Cached snapshot — ``useSyncExternalStore`` requires the same object
// identity for unchanged state. Without this cache React sees a brand
// new object literal every render and triggers an infinite re-render
// loop. The cache is invalidated only when ``setAgentProfile`` runs
// or a cross-tab storage event fires (see ``subscribeAgentProfile``).
let _cached: AgentProfilePrefs | null = null;

function _read(): AgentProfilePrefs {
  if (typeof window === "undefined") return DEFAULT_AGENT_PROFILE;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_AGENT_PROFILE;
    const parsed = JSON.parse(raw) as Partial<AgentProfilePrefs>;
    return {
      name: parsed.name || DEFAULT_AGENT_PROFILE.name,
      initial: (parsed.initial || DEFAULT_AGENT_PROFILE.initial).slice(0, 2),
      color: parsed.color || DEFAULT_AGENT_PROFILE.color,
      // ``avatar`` is optional; old profiles round-trip as ``undefined``
      // and downstream callers default to DiceBear ``shapes`` seeded
      // by ``name`` so the visual upgrade is automatic.
      avatar: parsed.avatar,
    };
  } catch {
    return DEFAULT_AGENT_PROFILE;
  }
}

/** Read the cached default-profile prefs. The cache is initialised on
 *  first call and refreshed only when ``setAgentProfile`` runs. */
export function getAgentProfile(): AgentProfilePrefs {
  if (_cached === null) _cached = _read();
  return _cached;
}

/** Write the default-profile prefs to localStorage, refresh the
 *  in-memory cache, and fire ``agent-profile-change`` so subscribers
 *  re-render. */
export function setAgentProfile(next: AgentProfilePrefs): void {
  if (typeof window === "undefined") return;
  _cached = {
    name: next.name || DEFAULT_AGENT_PROFILE.name,
    initial: (next.initial || DEFAULT_AGENT_PROFILE.initial).slice(0, 2),
    color: next.color || DEFAULT_AGENT_PROFILE.color,
    avatar: next.avatar,
  };
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(_cached));
  window.dispatchEvent(new Event(CHANGE_EVT));
}

/** Subscribe to default-profile changes. Returns an unsubscribe fn.
 *  Listens for both same-tab dispatches (``CHANGE_EVT``) and other-tab
 *  ``storage`` events so updates in one window propagate everywhere.
 *  Invalidates the cache on cross-tab storage events so the next
 *  ``getAgentProfile()`` re-reads from localStorage. */
export function subscribeAgentProfile(fn: () => void): () => void {
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

/* ---- React hook ------------------------------------------------ */

import { useSyncExternalStore } from "react";

/** Re-renders whenever the default-profile prefs change. Use in any
 *  component that displays the agent name / avatar so it picks up
 *  edits made in /settings/general live. */
export function useAgentProfile(): AgentProfilePrefs {
  return useSyncExternalStore(
    subscribeAgentProfile,
    getAgentProfile,
    () => DEFAULT_AGENT_PROFILE,
  );
}

/* ---- Per-message style ----------------------------------------- */

/** Is this an "anonymous" agent_id — i.e. the default profile shown
 *  in /settings? Centralised so the three helpers below stay in sync. */
function _isDefaultProfile(agentId: string | undefined | null): boolean {
  return !agentId || agentId === "main";
}

/** Pick a palette colour for an agent_id. For the default profile,
 *  returns the user-configured colour. For named agents, hashes to a
 *  stable palette slot. */
export function agentColor(agentId: string | undefined | null): string | null {
  if (_isDefaultProfile(agentId)) return getAgentProfile().color;
  return PALETTE[_hash(agentId!) % PALETTE.length];
}

/** One-character avatar text for an agent. Default profile reads from
 *  prefs; named agents take the first letter / digit of the id. */
export function agentInitial(agentId: string | undefined | null): string {
  if (_isDefaultProfile(agentId)) return getAgentProfile().initial;
  for (const ch of agentId!) {
    if (/[a-zA-Z0-9]/.test(ch)) return ch.toUpperCase();
  }
  return "A";
}

/** Display name for an agent. Default profile reads from prefs;
 *  named agents show their id verbatim. */
export function agentDisplayName(agentId: string | undefined | null): string {
  if (_isDefaultProfile(agentId)) return getAgentProfile().name;
  return agentId!;
}
