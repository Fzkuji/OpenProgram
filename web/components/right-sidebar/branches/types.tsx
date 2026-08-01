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

export interface BranchWindow {
  ws?: WebSocket;
  _branchesByConv?: Record<string, BranchRow[]>;
  _branchLaneColorMap?: Record<string, string>;
}

// Fallback palette — 唯一定义在 lib/format-utils/lane-colors.ts。
// 正常情况下每个分支的颜色来自 `_branchLaneColorMap`。
export { LANE_COLORS } from "@/lib/format-utils/lane-colors";

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
// the ``op:task-status`` window event (payload type: TaskStatusDetail
// in @/lib/net/ws-events). We keep tasks in non-terminal state
// ('queued' / 'running') in the map so the panel renders a branch as
// 'running'; when a terminal status arrives we flip the branch to
// 'finishing' for ~1.2s (matches the convFinishingWipe keyframe)
// before dropping it. Implementation lives inside the component so
// the state survives across panel mounts.

// Synthetic prefix for "pending branch" rows the panel renders while
// the task is in flight but no real assistant_msg_id exists yet.
// Distinct from real DAG ids (12 hex chars) so the click handlers
// can short-circuit safely.
export const PENDING_HEAD_PREFIX = "__pending_task__:";

