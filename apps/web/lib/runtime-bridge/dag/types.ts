/**
 * History DAG renderer — shared types + constants.
 *
 * Moved from ``apps/web/lib/runtime-bridge/history/types.ts`` as part of the
 * DAG module reorganisation. See ``./README.md`` for the full module
 * layout and ``docs/design/runtime/dag-node-model.md`` for the node-field
 * semantics (function, role, _tier, _depth, _lane).
 */

/* eslint-disable @typescript-eslint/no-explicit-any */

export interface GNode {
  id: string;
  predecessor?: string | null;
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
  _internal?: boolean;
  _runNode?: boolean;
  _overview_parent?: string | null;
  [k: string]: any;
}

// Square grid: same vertical and horizontal step so the DAG reads
// as a chessboard layout.
export const ROW_H = 32;
export const COL_W = 32;
export const NODE_R = 5;
export const PAD_X = 18;
export const PAD_Y = 16;

// Categorical branch palette — 唯一定义在 lib/format-utils/lane-colors.ts。
export { LANE_COLORS } from "@/lib/format-utils/lane-colors";

/** Layout parent: predecessor（对话链父）优先；没有对话前驱时 fallback 到
 *  caller（子调用/挂根父）。这是**布局父**，不是语义上的对话父——渲染器需要
 *  单一父指针才能把整图拼成一棵连通树。
 *
 *  fallback 服务的是两类没有 predecessor 的节点：函数内部的 code/llm 执行行
 *  （只有 caller）和 spawn 分支根（predecessor=None、caller=发起节点）。
 *  若只认 predecessor，它们会各自成为孤儿根，DAG 渲染成互不连通的多棵树。
 *
 *  注意：对话链上的首轮 user **不**属于这一类——它带 predecessor="ROOT"
 *  哨兵（见 webui/server.py 的 append 与 dag/overview.md §3），不靠 fallback。
 *  与 render/edges.ts 的 `predecessor || caller` 画边语义对齐。 */
export function semanticParent(n: GNode): string | null | undefined {
  return n.predecessor || n.caller;
}

export function layoutParent(n: GNode): string | null | undefined {
  return n._overview_parent || semanticParent(n);
}

export type HighlightMode = "viewport" | "context";
