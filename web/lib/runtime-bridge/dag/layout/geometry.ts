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

/** A sub-agent head (dag/rendering.md §12) — a dot whose name rides
 *  beside it, so its ink runs well right of the one column its tier
 *  buys. ``_spawnHW`` is the caption's full right reach in pixels, as
 *  measured by the renderer (``pipeline.ts`` stamps it from the same
 *  functions the drawer uses); the passes below key on it rather than
 *  on ``source`` so the layout and the glyph can never disagree about
 *  which nodes carry a caption. */
function spawnHW(n: GNode): number | undefined {
  const hw = (n as Record<string, unknown>)._spawnHW;
  return typeof hw === "number" ? hw : undefined;
}

export function computeGeometry(byId: Record<string, GNode>): Geometry {
  const ids = Object.keys(byId);

  // ── Columns: pack lanes by their widest visible tier ──
  // A sub-agent head counts for more than the one column its tier buys:
  // the caption beside it runs several columns to the right of the
  // anchor, and a lane sized to the tier alone lets it spill onto
  // whatever the next lane put there — in the case this was written from,
  // a following turn's node landed in the middle of the name. So the
  // head's tier is inflated to the columns its ink actually occupies,
  // and lane packing keeps working unchanged from there.
  const maxTierOfLane: Record<number, number> = Object.create(null);
  ids.forEach((id) => {
    const n = byId[id];
    const lane = n._lane || 0;
    const tier = typeof n._tier === "number" ? n._tier : 0;
    const hw = spawnHW(n);
    const width = hw === undefined
      ? tier
      : tier + Math.ceil(hw / COL_W);
    if (maxTierOfLane[lane] === undefined || width > maxTierOfLane[lane]) {
      maxTierOfLane[lane] = width;
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

  // ── Sub-agent heads never share a row (dag/rendering.md §12) ──
  // Every other node is a glyph one column wide, so a row can hold as
  // many of them as there are lanes. A sub-agent head is not: its name
  // rides beside it and runs a hundred-odd pixels to the right, and two
  // heads spawned by the same turn land one lane apart — near enough
  // that the first caption prints across the second dot. Rule ② packs
  // rows tight, so the fix is not a gap but an ordering: each head
  // takes the next row no other head has claimed, and its lane comes
  // with it. Sorting by call order keeps the two reading top-to-bottom
  // in the order they were spawned.
  const capsules = ids
    .filter((id) => spawnHW(byId[id]) !== undefined)
    .sort(byCallOrder);
  if (capsules.length > 1) {
    // Accumulate per lane: two capsules can share one (a nested spawn
    // the backend kept in its parent's lane), and the lane then owes the
    // sum of both their pushes, not whichever was written last.
    const laneShift: Record<number, number> = Object.create(null);
    let claimed = -Infinity;
    for (const id of capsules) {
      const lane = byId[id]._lane || 0;
      const row = (rowOf[id] || 0) + (laneShift[lane] || 0);
      const want = Math.max(row, claimed + 1);
      claimed = want;
      if (want !== row) laneShift[lane] = (laneShift[lane] || 0) + (want - row);
    }
    // Shift the capsule's whole lane, not the capsule alone: expanded,
    // the branch hangs off it and has to stay attached to its head.
    ids.forEach((id) => {
      const d = laneShift[byId[id]._lane || 0];
      if (d) rowOf[id] = (rowOf[id] || 0) + d;
    });
  }

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
    // A caption runs rightwards from its dot, well past the point the
    // layout placed it. Count that ink into the bounding box so a fit
    // never crops a name off the right edge.
    const hw = spawnHW(n);
    const right = hw === undefined ? x : x + hw;
    if (right > maxX) maxX = right;
    if (y > maxY) maxY = y;
  });

  return { pos, minX, maxX, maxY };
}
