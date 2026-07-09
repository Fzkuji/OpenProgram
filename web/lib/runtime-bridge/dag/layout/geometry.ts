/**
 * Content-driven pixel geometry for the visible DAG.
 *
 * The backend hands us integer ``_lane`` / ``_tier`` / ``_depth`` per
 * node. The old renderer projected those with a fixed
 * ``x = (lane + tier) * COL_W`` — so fork branches sat as far apart when
 * collapsed as when expanded (the backend reserves a fat lane gap for
 * each branch's worst-case tier width), and two call-tree siblings that
 * share a caller (same lane + tier + depth) landed on the exact same
 * pixel and overlapped — you could only ever click the one on top.
 *
 * This pass packs pixels from the *currently visible* nodes instead:
 *
 *   * Columns: each lane occupies ``maxVisibleTier(lane) + 1`` columns,
 *     lanes are packed left→right with a single gap column between them
 *     (the fork trunk lives in that gap, so ``forkX - COL_W`` still lands
 *     on it). Collapse a branch → its max tier shrinks → the lanes to its
 *     right slide back. Expand → they push out. Automatic, not fixed.
 *
 *   * Rows: backend ``_depth`` sets the base row (so fork siblings across
 *     lanes stay aligned at the fork point), but within one lane every
 *     visible node gets its own row — call-tree siblings that shared a
 *     depth are stacked instead of overlapping, so each is reachable and
 *     clickable.
 *
 * Returns a per-id ``{x, y}`` map plus the bounding box the caller uses
 * to size the SVG canvas.
 */

import { type GNode, COL_W, ROW_H, PAD_X, PAD_Y } from "../types";

export interface Geometry {
  pos: Record<string, { x: number; y: number }>;
  minX: number;
  maxX: number;
  maxY: number;
}

export function computeGeometry(byId: Record<string, GNode>): Geometry {
  const ids = Object.keys(byId);

  // ── Columns: pack lanes by their widest visible tier ──
  const maxTierOfLane: Record<number, number> = Object.create(null);
  ids.forEach((id) => {
    const n = byId[id];
    const lane = n._lane || 0;
    const tier = typeof n._tier === "number" ? n._tier : 0;
    if (maxTierOfLane[lane] === undefined || tier > maxTierOfLane[lane]) {
      maxTierOfLane[lane] = tier;
    }
  });
  const lanesSorted = Object.keys(maxTierOfLane)
    .map(Number)
    .sort((a, b) => a - b);
  const laneStartCol: Record<number, number> = Object.create(null);
  let col = 0;
  lanesSorted.forEach((lane, i) => {
    laneStartCol[lane] = col;
    // width of this lane + 1 gap column before the next lane (fork trunk)
    col += maxTierOfLane[lane] + 1 + (i < lanesSorted.length - 1 ? 1 : 0);
  });

  // ── Rows: base row from depth, de-collided within each lane ──
  // Compact the depth axis first so folded subtrees don't leave gaps.
  const depths = Array.from(
    new Set(ids.map((id) => (typeof byId[id]._depth === "number" ? byId[id]._depth! : 0))),
  ).sort((a, b) => a - b);
  const depthToRow: Record<number, number> = Object.create(null);
  depths.forEach((d, i) => { depthToRow[d] = i; });

  // Walk each lane's nodes in (depth, created_at) order, assigning rows
  // that never decrease and never repeat within the lane. A node inherits
  // its depth-row unless that row is already taken in the lane, in which
  // case it drops to the next free row (call-tree siblings stack).
  const rowOf: Record<string, number> = Object.create(null);
  const byLane: Record<number, string[]> = Object.create(null);
  ids.forEach((id) => {
    const lane = byId[id]._lane || 0;
    (byLane[lane] = byLane[lane] || []).push(id);
  });
  Object.keys(byLane).forEach((laneKey) => {
    const laneIds = byLane[Number(laneKey)];
    laneIds.sort((a, b) => {
      const da = typeof byId[a]._depth === "number" ? byId[a]._depth! : 0;
      const dbb = typeof byId[b]._depth === "number" ? byId[b]._depth! : 0;
      if (da !== dbb) return da - dbb;
      return (byId[a].created_at || 0) - (byId[b].created_at || 0);
    });
    let nextFree = -1;
    laneIds.forEach((id) => {
      const d = typeof byId[id]._depth === "number" ? byId[id]._depth! : 0;
      const base = depthToRow[d] ?? d;
      const row = Math.max(base, nextFree);
      rowOf[id] = row;
      nextFree = row + 1;
    });
  });

  const pos: Record<string, { x: number; y: number }> = Object.create(null);
  let minX = 0;
  let maxX = 0;
  let maxY = 0;
  ids.forEach((id) => {
    const n = byId[id];
    const lane = n._lane || 0;
    const tier = typeof n._tier === "number" ? n._tier : 0;
    const x = PAD_X + (laneStartCol[lane] + tier) * COL_W;
    const y = PAD_Y + (rowOf[id] || 0) * ROW_H;
    pos[id] = { x, y };
    if (x < minX) minX = x;
    if (x > maxX) maxX = x;
    if (y > maxY) maxY = y;
  });

  return { pos, minX, maxX, maxY };
}
