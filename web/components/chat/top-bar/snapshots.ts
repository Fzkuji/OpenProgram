"use client";

/**
 * Snapshots from legacy window globals → typed badge info objects
 * that the React top bar renders off of. Each helper is a pure read
 * — they never mutate window state. The corresponding writes live in
 * `legacy-bridge.ts`.
 */

import type {
  AgentSettingsSnapshot,
  BranchBadgeInfo,
  StatusBadgeInfo,
} from "@/lib/session-store";

export type LegacyTopbarGlobals = {
  _agentSettings?: {
    chat?: { provider?: string; model?: string; session_id?: string; locked?: boolean };
    exec?: { provider?: string; model?: string; session_id?: string; locked?: boolean };
  };
  _branchesByConv?: Record<
    string,
    Array<{ name?: string; active?: boolean }>
  >;
  currentSessionId?: string | null;
  isPaused?: boolean;
  isRunning?: boolean;
  _lastStatus?: string;
  _lastStatusSource?: string;
  // Originals captured before we wrap them, so the wrapper can chain.
  __origUpdateAgentBadges?: () => void;
  __origRefreshBranchBadge?: () => void;
  __origUpdateStatus?: (status: string, source?: string) => void;
  __origSetStatusDotHealth?: (state: string) => void;
  __origUpdateSendBtn?: () => void;
  __origRefreshStatusSource?: () => void;
  updateAgentBadges?: () => void;
  refreshBranchBadge?: () => void;
  updateStatus?: (status: string, source?: string) => void;
  setStatusDotHealth?: (state: string) => void;
  updateSendBtn?: () => void;
  refreshStatusSource?: () => void;
};

function legacyWindow(): LegacyTopbarGlobals {
  return window as unknown as LegacyTopbarGlobals;
}

export function snapshotAgentSettings(): AgentSettingsSnapshot {
  const src = legacyWindow()._agentSettings || {};
  return {
    chat: src.chat ? { ...src.chat } : undefined,
    exec: src.exec ? { ...src.exec } : undefined,
  };
}

export function snapshotBranchInfo(): BranchBadgeInfo {
  const w = legacyWindow();
  const sid = w.currentSessionId || null;
  if (!sid) return { visible: false, name: "main", count: 0 };
  const list = (w._branchesByConv && w._branchesByConv[sid]) || [];
  if (list.length === 0) return { visible: false, name: "main", count: 0 };
  const active = list.find((b) => b && b.active);
  const name = active && active.name ? active.name : "detached";
  return { visible: true, name, count: list.length };
}

/* The legacy ui.js owns the canonical mapping from (wsStatus, source,
   isPaused, isRunning) → (label, tone). We re-derive here in case the
   legacy DOM badge isn't around to read from. */
export function deriveStatusBadgeFromGlobals(): StatusBadgeInfo {
  const w = legacyWindow();
  if (w.isPaused) {
    return { label: "paused", tone: "warn", paused: true, title: "Paused" };
  }
  if (w.isRunning) {
    return { label: "running", tone: "ok", title: "Running" };
  }
  const ws = (window as unknown as { ws?: WebSocket }).ws;
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    const connecting = ws && ws.readyState === WebSocket.CONNECTING;
    return {
      label: connecting ? "connecting…" : "disconnected",
      tone: connecting ? "connecting" : "err",
      title: connecting ? "connecting…" : "disconnected",
    };
  }
  const source = w._lastStatusSource || "Local";
  return { label: source, tone: "ok", title: `connected · ${source}` };
}
