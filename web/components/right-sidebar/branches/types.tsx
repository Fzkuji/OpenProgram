/**
 * Shared types + small helpers for the right-rail branches panel.
 *
 * Pulled out of branches-panel.tsx so BranchItem + the main panel
 * can each import what they need without a single 783-line file.
 */

import { SquarePenIcon, XIcon } from "@/components/animated-icons";

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

// Branch row rename / delete glyphs → animated line icons (pqoqubbw,
// in components/animated-icons). Self-animate on hover (the action
// button is icon-sized). edit = square-pen, delete (×) = x.
export const RENAME_SVG = <SquarePenIcon size={16} />;
export const DEL_SVG = <XIcon size={16} />;



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

