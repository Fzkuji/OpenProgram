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
 *   * Rows: within a lane the visible call tree is walked in pre-order,
 *     one row per node — a parent's whole subtree stacks before its next
 *     sibling, so children of one parent form a vertical list in their
 *     shared column (the transcript's indent model) and every node is
 *     reachable and clickable. The lane's first node anchors at its
 *     backend ``_depth`` row so fork siblings across lanes align.
 *
 * Returns a per-id ``{x, y}`` map plus the bounding box the caller uses
 * to size the SVG canvas.
 */

import { type GNode, COL_W, ROW_H, PAD_X, PAD_Y, layoutParent } from "../types";

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

  // Walk each lane's call tree in PRE-ORDER, one row per visible node: a
  // parent's whole subtree stacks before its next sibling, so direct
  // children of one parent share the parent-tier+1 column and read as a
  // vertical list (the transcript's indent model). Sorting by depth
  // instead would interleave sibling subtrees into a diagonal staircase.
  // The lane's first node anchors at its depth-row so fork siblings
  // across lanes still align at the fork point.
  const rowOf: Record<string, number> = Object.create(null);
  const laneRoots: Record<number, string[]> = Object.create(null);
  const kidsOf: Record<string, string[]> = Object.create(null);
  ids.forEach((id) => {
    const n = byId[id];
    const lane = n._lane || 0;
    const parent = layoutParent(n);
    if (parent && byId[parent] && (byId[parent]._lane || 0) === lane) {
      (kidsOf[parent] = kidsOf[parent] || []).push(id);
    } else {
      (laneRoots[lane] = laneRoots[lane] || []).push(id);
    }
  });
  const byCallOrder = (a: string, b: string): number =>
    (byId[a].created_at || 0) - (byId[b].created_at || 0);
  Object.keys(laneRoots).forEach((laneKey) => {
    const roots = laneRoots[Number(laneKey)].slice().sort(byCallOrder);
    let next = -1;
    roots.forEach((rootId) => {
      const d = typeof byId[rootId]._depth === "number" ? byId[rootId]._depth! : 0;
      next = Math.max(next, depthToRow[d] ?? d);
      // Iterative pre-order: pop, assign, push children reversed so the
      // first child (by call order) is visited first.
      const stack = [rootId];
      while (stack.length) {
        const id = stack.pop()!;
        rowOf[id] = next++;
        const kids = (kidsOf[id] || []).slice().sort(byCallOrder);
        for (let i = kids.length - 1; i >= 0; i--) stack.push(kids[i]);
      }
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
