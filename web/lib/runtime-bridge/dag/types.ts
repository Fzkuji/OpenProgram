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

// Categorical branch palette — 唯一定义在 lib/format-utils/lane-colors.ts
// （叶子模块，本文件模块作用域取 window 不能被 SSR 侧引用）。
export { LANE_COLORS } from "@/lib/format-utils/lane-colors";

/** Layout parent: predecessor (对话链父)优先；没有对话前驱时 fallback 到
 *  caller（子调用/挂根父）。每轮/每分支的第一个 user 节点约定 predecessor
 *  为空、只靠 caller="ROOT" 挂在根上（见 webui/server.py 的 first-turn 注释）；
 *  若只认 predecessor，这些分支首节点会各自成为孤儿根，DAG 渲染成互不连通
 *  的多棵树、且 ROOT 子树悬空。与 render/edges.ts 的 `predecessor || caller`
 *  画边语义对齐，让树结构也把 caller 边算上，四条分支归到同一棵树。 */
export function layoutParent(n: GNode): string | null | undefined {
  return n.predecessor || n.caller;
}

export type HighlightMode = "viewport" | "context";
