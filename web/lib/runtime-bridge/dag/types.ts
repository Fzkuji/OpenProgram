/**
 * History DAG renderer — shared types + constants.
 *
 * Moved from ``web/lib/runtime-bridge/history/types.ts`` as part of the
 * DAG module reorganisation. See ``./README.md`` for the full module
 * layout and ``docs/design/runtime/dag-node-model.md`` for the node-field
 * semantics (function, role, _tier, _depth, _lane).
 */

/* eslint-disable @typescript-eslint/no-explicit-any */

export interface GNode {
  id: string;
  parent_id?: string | null;
  called_by?: string | null;
  role?: string;
  display?: string;
  created_at?: number;
  function?: string;
  name?: string;
  preview?: string;
  is_named?: boolean;
  head_msg_id?: string;
  children?: GNode[];
  _depth?: number;
  _lane?: number;
  _tier?: number;
  _anchor?: GNode;
  _internal?: boolean;
  _runNode?: boolean;
  [k: string]: any;
}

export interface HGWindow {
  currentSessionId?: string | null;
  _branchesByConv?: Record<string, GNode[]>;
  _branchLaneColorMap?: Record<string, string>;
  _postCheckoutScrollTo?: string | null;
  ws?: WebSocket | null;
  [k: string]: any;
}

export const HGW = window as unknown as HGWindow;

// Square grid: same vertical and horizontal step so the DAG reads
// as a chessboard layout.
export const ROW_H = 32;
export const COL_W = 32;
export const NODE_R = 5;
export const PAD_X = 18;
export const PAD_Y = 16;

// Index 0 is the trunk colour; 1..N-1 are side-branch colours, picked
// by a hash of the branch's leaf id. Distinct, evenly-spread hues so
// neighbouring branches never read as the same colour.
export const LANE_COLORS = [
  "#4f8ef7", // blue        (trunk)
  "#5aad4e", // green
  "#d4843a", // orange
  "#9d6fe0", // purple
  "#e0445a", // red
  "#2db3d5", // cyan
  "#e0b020", // gold
  "#35b89a", // teal
  "#e066b3", // magenta
  "#6b8dd6", // slate blue
  "#8fbf3f", // lime
  "#d9694f", // coral
  "#52c4c4", // aqua
  "#b08be0", // lavender
  "#c79a4a", // tan
  "#e08a3a", // amber
  "#6fae6f", // sage
  "#d05fa0", // rose
];

/** Layout parent: called_by (session DAG). */
export function layoutParent(n: GNode): string | null | undefined {
  return n.called_by;
}

export type HighlightMode = "viewport" | "context";
