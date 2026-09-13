"use client";

/**
 * Top-bar store sync: capture from the shared runtime state → typed
 * badge info, and push into the zustand session store so the React
 * chips re-render.
 *
 * Lives under `lib/` (not under the top-bar component dir) so that
 * `lib/runtime-bridge/{ui,providers,conversations}.ts` can import the
 * pushes without a cycle back through `components/`.
 */

import {
  useSessionStore,
  type AgentSettingsState,
  type BranchBadgeInfo,
  type StatusBadgeInfo,
} from "@/lib/session-store";
import { getSocket, runtimeState } from "@/lib/runtime-bridge/state";

type AgentSlot = {
  provider?: string;
  model?: string;
  session_id?: string;
  locked?: boolean;
};

type BranchRow = { name?: string; active?: boolean };

/* ---- last status, as reported by `updateStatus` -------------------- */

let lastStatus = "";
let lastStatusSource = "Local";

export function setLastStatus(status: string, source?: string): void {
  lastStatus = status;
  lastStatusSource = source || "Local";
}

export function getLastStatus(): { status: string; source: string } {
  return { status: lastStatus, source: lastStatusSource };
}

/* ---- captures ------------------------------------------------------ */

export function captureAgentSettings(): AgentSettingsState {
  const src = runtimeState._agentSettings;
  return {
    chat: src.chat ? { ...(src.chat as AgentSlot) } : undefined,
    exec: src.exec ? { ...(src.exec as AgentSlot) } : undefined,
  };
}

export function captureBranchInfo(): BranchBadgeInfo {
  const sid = runtimeState.currentSessionId;
  if (!sid) return { visible: false, name: "main", count: 0 };
  const list = (runtimeState._branchesByConv[sid] as BranchRow[]) || [];
  if (list.length === 0) return { visible: false, name: "main", count: 0 };
  const active = list.find((b) => b && b.active);
  const name = active && active.name ? active.name : "detached";
  return { visible: true, name, count: list.length };
}

/* `lib/runtime-bridge/ui.ts` owns the canonical mapping from (wsStatus,
   source, isPaused, isRunning) → (label, tone). We re-derive here for
   the pushes that carry no explicit override. */
export function deriveStatusBadge(): StatusBadgeInfo {
  if (runtimeState.isPaused) {
    return { label: "paused", tone: "warn", paused: true, title: "Paused" };
  }
  if (runtimeState.isRunning) {
    return { label: "running", tone: "ok", title: "Running" };
  }
  const ws = getSocket();
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    const connecting = ws && ws.readyState === WebSocket.CONNECTING;
    return {
      label: connecting ? "connecting…" : "disconnected",
      tone: connecting ? "connecting" : "err",
      title: connecting ? "connecting…" : "disconnected",
    };
  }
  const source = lastStatusSource || "Local";
  // Strip the trailing ` · title` segment that `refreshStatusSource`
  // appends — we only want channel / account info on the badge, not
  // the conversation's title text.
  return { label: badgeLabelFromSource(source), tone: "ok", title: `connected · ${source}` };
}

/**
 * `refreshStatusSource` builds the badge text by joining
 * `channel · account · title` with ` · `. The title slot would show the
 * conversation's first message in the badge, which isn't what we want —
 * the badge should show connection / channel state, not chat content.
 * Take only the first segment for the label; the full source string
 * stays in the tooltip.
 */
export function badgeLabelFromSource(source: string | undefined): string {
  if (!source) return "Local";
  const idx = source.indexOf(" · ");
  return idx >= 0 ? source.slice(0, idx) : source;
}

/* ---- store pushes -------------------------------------------------- */

export function pushAgentSettings(): void {
  try {
    useSessionStore.getState().setAgentSettings(captureAgentSettings());
  } catch {
    /* ignore — store not ready yet */
  }
}

export function pushBranchInfo(): void {
  try {
    useSessionStore.getState().setBranchInfo(captureBranchInfo());
  } catch {
    /* ignore */
  }
}

export function pushStatusBadge(override?: Partial<StatusBadgeInfo>): void {
  try {
    const store = useSessionStore.getState();
    const socket = getSocket();
    // Connection-dependent controls must follow transport state, not the
    // running/paused badge or a refresh of the conversation's source label.
    store.setWsStatus(socket?.readyState === WebSocket.OPEN ? "open"
      : socket && socket.readyState === WebSocket.CONNECTING ? "connecting" : "closed");
    store.setStatusBadge({ ...deriveStatusBadge(), ...override });
  } catch {
    /* ignore */
  }
}
