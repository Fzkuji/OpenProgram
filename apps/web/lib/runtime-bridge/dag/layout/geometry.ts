/**
 * Content-driven pixel geometry for the visible DAG.
 *
 * Two node populations, two placement rules:
 *
 *   * CHAIN nodes (root / user / reply / merge) keep the proven
 *     lane/tier/depth pack: each lane occupies its visible tier span,
 *     lanes pack left→right, rows walk each lane's call tree in
 *     pre-order. A fork lane sits TWO columns from the lane it forked
 *     off (one gap column — the branch is a parallel version, and the
 *     air says so), and a fork root shares its sibling's ROW: the
 *     branch extends right, level with the turn it rewrites
 *     (dag/rendering.md scene 3).
 *
 *   * THREAD items (execution squares, spawn heads) hang on their
 *     anchor's call thread: one column right of the anchor, one row
 *     per event in call order. An open spawn's own thread nests one
 *     column further right, and its rows push the parent's later
 *     items down. After a chain-anchor thread is seated, later chain
 *     rows (and already-placed thread rows below the insertion) shift
 *     down by the occupied span, and each assigned thread column is
 *     reserved — expansion is insertion, not overlay. Placement is
 *     recursive because the model is.
 *
 * Returns per-id ``{x, y}`` plus the thread columns/rows the edge
 * drawer needs to ink the dotted lines, and the fork-sibling map the
 * bridge drawer keys on.
 */

import { type GNode, COL_W, ROW_H, PAD_X, PAD_Y, layoutParent } from "../types";
import { isChainNode, type ThreadModel } from "../passes/thread";

export interface Geometry {
  pos: Record<string, { x: number; y: number }>;
  minX: number;
  maxX: number;
  maxY: number;
  /** ``anchorId → thread column`` (grid units) for every OPEN thread. */
  threadColOf: Record<string, number>;
  /** ``anchorId → row index per thread item``, aligned with the
   *  anchor's event list. */
  threadRowsOf: Record<string, number[]>;
  /** ``forkRootId → the sibling it runs parallel to`` — present only
   *  when the two share a row and the bridge can be a straight dash. */
  forkSibOf: Record<string, string>;
  /** ``lane → its spine`` for every fork lane: the empty column the
   *  bridge lands on and the turns stub off (x), starting at the fork
   *  root's row (topY). The trunk's equivalent is ROOT's own glyph. */
  laneSpineOf: Record<number, { x: number; topY: number }>;
}

export function computeGeometry(
  byId: Record<string, GNode>,
  thread: ThreadModel,
): Geometry {
  const ids = Object.keys(byId);
  const chainIds = ids.filter((id) => isChainNode(byId[id], byId));

  // ── Columns: pack lanes by their widest visible CHAIN tier ──
  // Thread items reserve no lane width — their column is chosen after
  // the lanes are down, in the first free column right of the anchor.
  const lanesOf: Record<number, string[]> = Object.create(null);
  chainIds.forEach((id) => {
    const lane = byId[id]._lane || 0;
    (lanesOf[lane] = lanesOf[lane] || []).push(id);
  });
  const laneKeys = Object.keys(lanesOf).map(Number).sort((a, b) => a - b);

  // A lane that starts with a fork root keeps one gap column from the
  // lane to its left: the branch runs parallel, two grid units out.
  const forkLanes = new Set<number>();
  chainIds.forEach((id) => {
    const n = byId[id];
    const p = layoutParent(n);
    if (p && byId[p] && (byId[p]._lane || 0) !== (n._lane || 0)) {
      forkLanes.add(n._lane || 0);
    }
  });

  // A fork lane mirrors the trunk (dag/rendering.md scene 3): the
  // lane's SPINE column carries no glyph — every chain node steps one
  // column right of it, the dashed bridge lands on the spine, and the
  // turns stub off the spine exactly as trunk turns stub off ROOT's
  // line. Without the empty spine column the branch started WITH a
  // node and later turns read as chained user messages.
  const startCol: Record<number, number> = Object.create(null);
  const minTierOf: Record<number, number> = Object.create(null);
  let col = 0;
  laneKeys.forEach((lane) => {
    const own = lanesOf[lane];
    // Per-lane tier zeroing: the backend hands fork roots the tier of
    // their old in-lane position; without zeroing a one-node branch
    // arrives several columns adrift of its lane.
    minTierOf[lane] = Math.min(...own.map((id) => byId[id]._tier || 0));
    const maxTier = Math.max(...own.map((id) => byId[id]._tier || 0));
    if (forkLanes.has(lane) && col > 0) col += 1;
    startCol[lane] = col;
    // +1: the fork lane's empty spine column.
    col += maxTier - minTierOf[lane] + 1 + (forkLanes.has(lane) ? 1 : 0);
  });
  const colOf = (id: string): number => {
    const n = byId[id];
    const lane = n._lane || 0;
    return (startCol[lane] || 0) + (n._tier || 0) - (minTierOf[lane] || 0)
      + (forkLanes.has(lane) ? 1 : 0);
  };
  const usedCols = new Set<number>();
  laneKeys.forEach((lane) => {
    lanesOf[lane].forEach((id) => usedCols.add(colOf(id)));
  });

  // ── Rows: depth-compressed, then per-lane pre-order ──
  const depths = Array.from(new Set(
    chainIds.map((id) =>
      typeof byId[id]._depth === "number" ? byId[id]._depth! : 0),
  )).sort((a, b) => a - b);
  const depthToRow: Record<number, number> = Object.create(null);
  depths.forEach((d, i) => { depthToRow[d] = i; });

  const rowOf: Record<string, number> = Object.create(null);
  const laneRoots: Record<number, string[]> = Object.create(null);
  const kidsOf: Record<string, string[]> = Object.create(null);
  chainIds.forEach((id) => {
    const n = byId[id];
    const lane = n._lane || 0;
    const parent = layoutParent(n);
    if (parent && byId[parent] && isChainNode(byId[parent], byId)
        && (byId[parent]._lane || 0) === lane) {
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
      const d = typeof byId[rootId]._depth === "number"
        ? byId[rootId]._depth! : 0;
      next = Math.max(next, depthToRow[d] ?? d);
      const stack = [rootId];
      while (stack.length) {
        const id = stack.pop()!;
        rowOf[id] = next++;
        const kids = (kidsOf[id] || []).slice().sort(byCallOrder);
        for (let i = kids.length - 1; i >= 0; i--) stack.push(kids[i]);
      }
    });
  });

  // ── Fork roots share their sibling's row (scene 3) ──
  // A branch is a parallel VERSION of the turn beside it, so it sits
  // level with that turn and extends right. Every branch off one fork
  // point is a version of the same turn, so they all share that one row
  // and read as the alternatives they are — retry #3 on a row of its own
  // below #2 reads as something that happened after it.
  const forkSibOf: Record<string, string> = Object.create(null);
  // The last branch placed off each fork point. Branch N bridges to
  // branch N-1, the one immediately to its left, rather than reaching
  // back to the trunk across everything already between them.
  const lastForkOf: Record<string, string> = Object.create(null);
  const forkRoots = chainIds
    .filter((id) => {
      const p = layoutParent(byId[id]);
      return p && byId[p] && isChainNode(byId[p], byId)
        && (byId[p]._lane || 0) !== (byId[id]._lane || 0);
    })
    .sort(byCallOrder);
  forkRoots.forEach((id) => {
    const pid = layoutParent(byId[id])!;
    const trunkSib = chainIds.find((cid) => cid !== id
      && layoutParent(byId[cid]) === pid
      && (byId[cid]._lane || 0) === (byId[pid]._lane || 0));
    const sib = lastForkOf[pid] ?? trunkSib;
    if (sib) forkSibOf[id] = sib;
    lastForkOf[pid] = id;
    // A fork whose parent is the compaction capsule forked from a turn
    // INSIDE the folded range — the capsule is its origin, not a
    // sibling, so the branch runs level with the capsule itself and
    // the bridge is a straight dash on that row (dag/rendering.md §9).
    const parentIsCapsule = Array.isArray(
      (byId[pid] as Record<string, unknown>).covers_ids);
    const want = parentIsCapsule
      ? (rowOf[pid] || 0)
      : trunkSib !== undefined
        ? rowOf[trunkSib]
        : (rowOf[pid] || 0) + 1;
    const delta = want - (rowOf[id] || 0);
    if (delta) {
      const lane = byId[id]._lane || 0;
      chainIds.forEach((cid) => {
        if ((byId[cid]._lane || 0) === lane) {
          rowOf[cid] = (rowOf[cid] || 0) + delta;
        }
      });
    }
  });
  // Each fork lane's spine starts at its (topmost) fork root's row.
  const laneTopRow: Record<number, number> = Object.create(null);
  forkRoots.forEach((id) => {
    const lane = byId[id]._lane || 0;
    const r = rowOf[id] || 0;
    if (laneTopRow[lane] === undefined || r < laneTopRow[lane]) {
      laneTopRow[lane] = r;
    }
  });

  // ── Threads: recursive placement ──
  // Items run from the anchor's next row (past any fork rows hanging
  // off it) down one row per event; an open spawn recurses one column
  // further right and its rows push everything after it down. After
  // each chain-anchor place(), later chain rows (and already-placed
  // thread rows at/below the insertion) shift down by the occupied
  // span — otherwise later triangles stay on the thread's rows and
  // the vertical in the anchor's column runs through them. Each
  // assigned thread column is reserved immediately so a second open
  // thread walks to the next free column instead of stacking.
  const threadColOf: Record<string, number> = Object.create(null);
  const threadRowsOf: Record<string, number[]> = Object.create(null);
  const place = (anchor: string, baseCol: number, startRow: number): number => {
    let c = baseCol;
    while (usedCols.has(c)) c++;
    threadColOf[anchor] = c;
    usedCols.add(c);
    let cursor = startRow;
    const rows: number[] = [];
    (thread.events[anchor] || []).forEach((ev) => {
      if (!byId[ev.id]) return; // event's node not in the visible graph
      rows.push(cursor);
      rowOf[ev.id] = cursor;
      cursor += 1;
      if (ev.kind === "spawn" && thread.isOpen(ev.id)
          && (thread.events[ev.id] || []).length) {
        cursor = place(ev.id, c + 1, cursor);
      }
    });
    threadRowsOf[anchor] = rows;
    return cursor;
  };
  const shiftRowsFrom = (
    startRow: number,
    delta: number,
    skip: Set<string>,
  ): void => {
    if (!delta) return;
    chainIds.forEach((cid) => {
      if (skip.has(cid)) return;
      if ((rowOf[cid] || 0) >= startRow) {
        rowOf[cid] = (rowOf[cid] || 0) + delta;
      }
    });
    Object.keys(threadRowsOf).forEach((anchor) => {
      if (skip.has(anchor)) return;
      const rows = threadRowsOf[anchor];
      let moved = false;
      const next = rows.map((r) => {
        if (r >= startRow) {
          moved = true;
          return r + delta;
        }
        return r;
      });
      if (!moved) return;
      threadRowsOf[anchor] = next;
      (thread.events[anchor] || []).forEach((ev, i) => {
        if (i < next.length && byId[ev.id]) rowOf[ev.id] = next[i];
      });
    });
    Object.keys(laneTopRow).forEach((laneKey) => {
      if ((laneTopRow[Number(laneKey)] || 0) >= startRow) {
        laneTopRow[Number(laneKey)] += delta;
      }
    });
  };
  // Earlier anchors insert first so a later open thread places against
  // already-shifted chain rows.
  const openAnchors = chainIds
    .filter((id) => thread.isOpen(id) && (thread.events[id] || []).length)
    .sort((a, b) => (rowOf[a] || 0) - (rowOf[b] || 0) || colOf(a) - colOf(b));
  openAnchors.forEach((id) => {
    // Right of the anchor — and right of any branch bridging off it,
    // so the dotted line never cuts the same-row bridge.
    let base = colOf(id) + 1;
    let hasFork = false;
    forkRoots.forEach((fid) => {
      if (layoutParent(byId[fid]) === id) {
        base = Math.max(base, colOf(fid) + 1);
        hasFork = true;
      }
    });
    // One row of clearance when anything forks off this anchor — they all
    // share a single row now, however many there are.
    const startRow = (rowOf[id] || 0) + 1 + (hasFork ? 1 : 0);
    const placedBefore = new Set(Object.keys(threadRowsOf));
    const cursor = place(id, base, startRow);
    const justPlaced = new Set(
      Object.keys(threadRowsOf).filter((a) => !placedBefore.has(a)),
    );
    justPlaced.add(id);
    shiftRowsFrom(startRow, cursor - startRow, justPlaced);
  });

  // ── Pixels ──
  const pos: Record<string, { x: number; y: number }> = Object.create(null);
  let minX = 0;
  let maxX = 0;
  let maxY = 0;
  const seat = (id: string, x: number, y: number): void => {
    pos[id] = { x, y };
    if (x < minX) minX = x;
    if (x > maxX) maxX = x;
    if (y > maxY) maxY = y;
  };
  chainIds.forEach((id) => {
    seat(id, PAD_X + colOf(id) * COL_W, PAD_Y + (rowOf[id] || 0) * ROW_H);
  });
  Object.keys(threadRowsOf).forEach((anchor) => {
    const c = threadColOf[anchor];
    const rows = threadRowsOf[anchor];
    (thread.events[anchor] || []).forEach((ev, i) => {
      if (i >= rows.length || !byId[ev.id]) return;
      seat(ev.id, PAD_X + c * COL_W, PAD_Y + rows[i] * ROW_H);
    });
  });
  // Anything left (defensive: nodes the thread model didn't claim)
  // falls back to its lane/tier/depth seat so it is at least on screen.
  ids.forEach((id) => {
    if (pos[id]) return;
    const n = byId[id];
    const lane = n._lane || 0;
    const x = PAD_X + ((startCol[lane] || 0) + (n._tier || 0)) * COL_W;
    const d = typeof n._depth === "number" ? n._depth : 0;
    seat(id, x, PAD_Y + (depthToRow[d] ?? d) * ROW_H);
  });

  const laneSpineOf: Record<number, { x: number; topY: number }> =
    Object.create(null);
  Object.keys(laneTopRow).forEach((laneKey) => {
    const lane = Number(laneKey);
    laneSpineOf[lane] = {
      x: PAD_X + (startCol[lane] || 0) * COL_W,
      topY: PAD_Y + laneTopRow[lane] * ROW_H,
    };
  });

  return {
    pos, minX, maxX, maxY, threadColOf, threadRowsOf, forkSibOf, laneSpineOf,
  };
}
