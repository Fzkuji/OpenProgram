/**
 * Shared types + small helpers for the right-rail branches panel.
 *
 * Pulled out of branches-panel.tsx so BranchItem + the main panel
 * can each import what they need without a single 783-line file.
 */

export interface BranchRow {
  head_msg_id: string;
  name?: string;
  active?: boolean;
}

export interface ConvSummary {
  id: string;
  title?: string;
  channel?: string | null;
  account_id?: string | null;
}

export interface BranchWindow {
  ws?: WebSocket;
  _branchesByConv?: Record<string, BranchRow[]>;
  _branchLaneColorMap?: Record<string, string>;
  conversations?: Record<string, ConvSummary>;
}

// Fallback palette — kept in sync with history-graph.ts LANE_COLORS.
// Normally the per-branch colour comes from `_branchLaneColorMap`.
export const LANE_COLORS = [
  "#4f8ef7", "#5aad4e", "#d4843a", "#9d6fe0", "#e0445a", "#2db3d5",
  "#e0b020", "#35b89a", "#e066b3", "#6b8dd6", "#8fbf3f", "#d9694f",
  "#52c4c4", "#b08be0", "#c79a4a", "#e08a3a", "#6fae6f", "#d05fa0",
];

export function wsSend(payload: unknown): void {
  const w = window as unknown as BranchWindow;
  if (w.ws && w.ws.readyState === WebSocket.OPEN) {
    w.ws.send(JSON.stringify(payload));
  }
}

export const RENAME_SVG = (
  <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M11.5 2.5l2 2L5 13l-3 1 1-3 8.5-8.5z" />
  </svg>
);
export const DEL_SVG = (
  <svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
    <line x1="2" y1="2" x2="8" y2="8" />
    <line x1="8" y1="2" x2="2" y2="8" />
  </svg>
);



// Per-session map of task_id → {target_head, status} mirrored from
// the ``op:task-status`` window event. We keep tasks in non-terminal
// state ('queued' / 'running') in the map so the panel renders a
// branch as 'running'; when a terminal status arrives we flip the
// branch to 'finishing' for ~1.2s (matches the convFinishingWipe
// keyframe) before dropping it. Implementation lives inside the
// component so the state survives across panel mounts.
export interface TaskStatusDetail {
  task_id?: string;
  session_id?: string;
  target_branch_head_id?: string | null;
  head_id?: string | null;
  status?: string;
  label?: string | null;
  subject?: string | null;
}

// Synthetic prefix for "pending branch" rows the panel renders while
// the task is in flight but no real assistant_msg_id exists yet.
// Distinct from real DAG ids (12 hex chars) so the click handlers
// can short-circuit safely.
export const PENDING_HEAD_PREFIX = "__pending_task__:";

