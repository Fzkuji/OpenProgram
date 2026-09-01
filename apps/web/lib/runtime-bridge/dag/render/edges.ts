/**
 * Renderer: edge SVG drawing.
 *
 * Every line is drawn CENTRE to CENTRE and the node glyphs paint over
 * the ends: glyph edges sit at different distances per shape, and any
 * fixed stand-off gap eventually shows daylight against a sloped
 * triangle side. The background-filled glyph covers the line inside
 * its own outline, so every joint is seamless for every shape.
 *
 * Edge kinds:
 *   * conv chain — solid coloured, vertical trunk + horizontal step.
 *   * fork bridge — dashed horizontal, sibling → fork root, on their
 *     shared row (scene 3); an elbow fallback when rows diverge.
 *   * call thread — faint dotted line off the anchor's shoulder, down
 *     the thread column to its last item (dag/rendering.md §12).
 *   * attach / merge references — unchanged.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */

import { type GNode, COL_W, PAD_X, PAD_Y, ROW_H } from "../types";
import { _branchColor, _edgePath, _svg } from "./shapes";
import { _onEdgeDblclick } from "../interaction/nodes";
import { coversIds } from "../passes/fold-summaries";
import { isChainNode, isSpawnRoot } from "../passes/thread";
import type { Geometry } from "../layout/geometry";

const GHOST_STROKE = "var(--dag-ghost, #c9c7bf)";

export function drawEdges(
  edgeG: SVGElement,
  tree: { byId: Record<string, GNode> },
  graphIn: GNode[],
  pos: (n: GNode) => { x: number; y: number },
  stableLeafOfNode: Record<string, string>,
  geom: Geometry,
): void {
  // An expanded capsule's range draws as a dashed grey spur off the
  // trunk (dag/rendering.md §9): the turns are back on screen, but the
  // line has to keep saying they are not what the next request carries.
  // Per-branch: the fold pass stamps ``_ghost`` only when the summary
  // applies to the branch being viewed — on other branches these same
  // turns are live context and keep their colour.
  const rootNode = Object.values(tree.byId).find((n) => n.display === "root");
  const rootPos = rootNode ? pos(rootNode) : null;

  const fullById: Record<string, GNode> = Object.create(null);
  graphIn.forEach((m) => { fullById[m.id] = m; });

  const forkNodes: string[] = [];

  // ── Conv-chain edges ──
  Object.keys(tree.byId).forEach((id) => {
    const node = tree.byId[id];
    if (node.display === "root") return;
    // A spawn head hangs on its owner's thread; the thread line below
    // is its only edge. Execution nodes likewise — their ink is the
    // thread line, not a chain edge.
    if (isSpawnRoot(node) || !isChainNode(node)) return;
    let pid = node.predecessor || node.caller;
    if (pid && !tree.byId[pid]) {
      let cur = pid;
      let hops = 0;
      while (cur && !tree.byId[cur] && hops < 50) {
        const pn = fullById[cur];
        cur = pn ? (pn.predecessor || pn.caller || null) : null;
        hops++;
      }
      if (cur && tree.byId[cur]) pid = cur;
      else return;
    }
    if (!pid || !tree.byId[pid]) return;
    const parent = tree.byId[pid];
    const sameLane = (node._lane || 0) === (parent._lane || 0);
    if (!sameLane) {
      forkNodes.push(id);
      // A fork-lane USER root still draws its own spine stub below —
      // the bridge only reaches the lane's spine, and the stub is what
      // hangs the turn off it, same as every later turn. Capsules and
      // relics keep the bridge as their only ink.
      const spine = geom.laneSpineOf[node._lane || 0];
      if (!spine || node.role !== "user") return;
    }
    const c = pos(node);
    // Ghost turns keep their branch colour — "out of context" is said
    // by the dashes (and by the missing white fill on the glyph), not
    // by draining the colour.
    const isGhost = !!(node as Record<string, unknown>)._ghost;
    const color = _branchColor(node, stableLeafOfNode);
    const dash: Record<string, string> = isGhost
      ? { "stroke-dasharray": "3 3" }
      : {};

    const p = pos(parent);
    // A capsule rides the trunk like a turn does (it stands in for
    // whole turns and sits on the turn column) — its edge comes off
    // the trunk vertical, not down from the reply glyph above it.
    const isUserNode = node.role === "user" || !!coversIds(node);
    let trunkX = p.x;
    let fromY = p.y;
    if (isUserNode) {
      const myLane = node._lane || 0;
      const spine = geom.laneSpineOf[myLane];
      if (spine) {
        // Fork lane: turns stub off the lane's empty spine column,
        // whose line starts where the bridge lands (the fork root's
        // row) — the trunk pattern, with the spine top standing where
        // ROOT's glyph stands on lane 0.
        trunkX = spine.x;
        fromY = spine.topY;
      } else if (rootPos && myLane === (rootNode?._lane || 0)) {
        trunkX = rootPos.x;
        fromY = rootPos.y;
      } else {
        trunkX = c.x;
      }
    }

    if (c.y > fromY) {
      edgeG.appendChild(_svg("line", {
        x1: trunkX, y1: fromY, x2: trunkX, y2: c.y,
        stroke: color, "stroke-width": 1.6, "stroke-linecap": "round",
        "pointer-events": "none", class: "history-edge", ...dash,
      }));
    }
    if (c.x !== trunkX) {
      edgeG.appendChild(_svg("line", {
        x1: trunkX, y1: c.y, x2: c.x, y2: c.y,
        stroke: color, "stroke-width": 1.6, "stroke-linecap": "round",
        "pointer-events": "none", class: "history-edge", ...dash,
      }));
    }
  });

  // ── Fork bridges (scene 3) ──
  // The branch root sits level with the sibling it parallels; the
  // bridge is a dashed horizontal between them. When the rows diverged
  // (no sibling, or later passes moved one), an elbow: down from the
  // fork point, then across into the root's row.
  const forkRoots: Record<number, GNode> = Object.create(null);
  for (const id of forkNodes) {
    const node = tree.byId[id];
    if (!node) continue;
    const myLane = node._lane || 0;
    if (!forkRoots[myLane]
        || (node._depth || 0) < (forkRoots[myLane]._depth || 0)) {
      forkRoots[myLane] = node;
    }
  }
  for (const id of forkNodes) {
    const node = tree.byId[id];
    if (!node) continue;
    const myLane = node._lane || 0;
    if (forkRoots[myLane]?.id !== id) continue;
    const d = pos(node);
    const color = _branchColor(node, stableLeafOfNode);
    const sibId = geom.forkSibOf[id];
    const sib = sibId ? tree.byId[sibId] : undefined;
    // A superseded summary's only meaningful attachment is its splice
    // point (ROOT for a from-the-start compaction). The sibling
    // straight-dash reads as "forked off that message" — wrong story
    // for a relic — so it always takes the elbow from its fork point.
    const isRelic = !!(node as Record<string, unknown>).superseded_summary;
    // Resolve the fork parent up front: a branch whose parent is (or
    // folded into) the CAPSULE must ink its line to the capsule, not to
    // the kept-tail sibling — it forked from inside the covered range,
    // and the capsule is what stands for that range now.
    let fp = node.predecessor || node.caller || "";
    let fhops = 0;
    while (fp && !tree.byId[fp] && fhops < 50) {
      const fn = fullById[fp];
      fp = fn ? (fn.predecessor || fn.caller || "") : "";
      fhops++;
    }
    const fpNode = fp ? tree.byId[fp] : undefined;
    const fpIsCapsule = !!(fpNode && coversIds(fpNode));
    // The bridge lands on the lane's SPINE (the empty column the turns
    // stub off), not on a node — the branch starts as a line, same as
    // the trunk starts at ROOT's line (dag/rendering.md scene 3). Only
    // capsules and relics — lanes with no user turns of their own —
    // keep the node itself as the bridge target.
    const spine = geom.laneSpineOf[myLane];
    const nodeTarget = isRelic || !!coversIds(node) || !spine;
    const ex = nodeTarget ? d.x : spine!.x;
    const ey = nodeTarget ? d.y : spine!.topY;
    // Straight dash when the origin sits on the spine-top row: from the
    // capsule it forked inside of (level-with-origin says so), or from
    // the sibling turn it parallels.
    const origin = fpIsCapsule ? fpNode : (!isRelic && sib) ? sib : undefined;
    if (origin && pos(origin).y === ey) {
      const sp = pos(origin);
      edgeG.appendChild(_svg("line", {
        x1: sp.x, y1: ey, x2: ex, y2: ey,
        stroke: color, "stroke-width": 1.5, "stroke-linecap": "round",
        "stroke-dasharray": "6 4", opacity: 0.7,
        "pointer-events": "none", class: "history-edge fork-edge",
      }));
      continue;
    }
    // Elbow — from the fork point itself, when a later pass has moved
    // the two off the shared row (a thread pushing rows down, say).
    if (!fpNode) continue;
    // Root-parented forks normally need no bridge (the user-node trunk
    // logic anchors lane 0 to the root glyph) — but a folded capsule
    // hanging off root is the trunk's visible start, and skipping it
    // leaves the root floating with no ink to its own session.
    if (fpNode.display === "root" && !coversIds(node)
        && !(node as Record<string, unknown>).superseded_summary) continue;
    const s = pos(fpNode);
    const vx = s.x + 14;
    const r = 10;
    edgeG.appendChild(_svg("path", {
      d: `M ${s.x} ${s.y} Q ${vx} ${s.y + 12} ${vx} ${s.y + 24} `
        + `L ${vx} ${ey - r} Q ${vx} ${ey} ${vx + r} ${ey} `
        + `L ${ex} ${ey}`,
      stroke: color, "stroke-width": 1.5, fill: "none",
      "stroke-linecap": "round",
      "stroke-dasharray": "6 4", opacity: 0.7,
      "pointer-events": "none", class: "history-edge fork-edge",
    }));
  }

  // ── Call threads (dag/rendering.md §12) ──
  // A faint dotted line from the anchor down its thread column to the
  // last item. Drawn under the nodes, so every square and triangle on
  // the line covers its own crossing.
  Object.keys(geom.threadRowsOf).forEach((anchor) => {
    const anchorNode = tree.byId[anchor];
    const rows = geom.threadRowsOf[anchor];
    if (!anchorNode || !rows.length) return;
    const ap = pos(anchorNode);
    const tx = PAD_X + geom.threadColOf[anchor] * COL_W;
    const lastY = PAD_Y + rows[rows.length - 1] * ROW_H;
    // The trunk pattern, one level down: a solid vertical drops the
    // anchor's own column, and every item gets a horizontal stub into
    // its row — down first, then right, exactly like chain edges, in
    // the anchor's own branch colour. The execution layer is already
    // said by the shapes and the column; the line needs no third voice.
    const tColor = _branchColor(anchorNode, stableLeafOfNode);
    edgeG.appendChild(_svg("line", {
      x1: ap.x, y1: ap.y, x2: ap.x, y2: lastY,
      stroke: tColor, "stroke-width": 1.4,
      "stroke-linecap": "round",
      "pointer-events": "none", class: "history-edge thread-edge",
    }));
    rows.forEach((r) => {
      const ry = PAD_Y + r * ROW_H;
      edgeG.appendChild(_svg("line", {
        x1: ap.x, y1: ry, x2: tx, y2: ry,
        stroke: tColor, "stroke-width": 1.4,
        "stroke-linecap": "round",
        "pointer-events": "none", class: "history-edge thread-edge",
      }));
    });
  });

  // ── Attach / merge reference edges ──
  // attach 指针节点不画（rendering.md 场景 8/10）；回流长虚线只在两端
  // 都可见时画——agent 内部节点归并进三角形后，指向它们的 ref 自然
  // 消失，返回关系由 spawn 头在线程上的位置表达。merge 节点在 tree 里
  // （◉），汇入线按 peer 分支色加粗实线（场景 8）。
  const refPairs: Array<{ ref: string; anchorId: string; isMerge: boolean }> = [];
  Object.keys(tree.byId).forEach((id) => {
    const n = tree.byId[id];
    const returns = (n as Record<string, unknown>).attach_returns as
      string[] | undefined;
    (returns || []).forEach((ref) => {
      refPairs.push({ ref, anchorId: id, isMerge: false });
    });
    if (n.function === "merge" && n.attach_ref) {
      refPairs.push({ ref: String(n.attach_ref), anchorId: id, isMerge: true });
    }
  });
  refPairs.forEach(({ ref, anchorId, isMerge }) => {
    const src = tree.byId[ref];
    const anchorNode = tree.byId[anchorId];
    if (!src || !anchorNode) return;
    // An agent's tip surfaced as a THREAD item: its return relationship
    // is structural — the spawn square's position on the anchor's
    // thread (§12) — so the merge-back dashes stay suppressed exactly
    // as they were while the tip was folded away.
    if ((src as Record<string, unknown>)._agentTurn) return;
    const srcPos = pos(src);
    const anchorPos = pos(anchorNode);
    const color = _branchColor(src, stableLeafOfNode);
    const ahit = _svg("path", {
      d: _edgePath(srcPos.x, srcPos.y, anchorPos.x, anchorPos.y),
      stroke: "transparent", "stroke-width": 14, fill: "none",
      "pointer-events": "stroke", "data-target-id": ref,
      class: "history-edge-hit attach-edge-hit",
    });
    (ahit as SVGGraphicsElement).style.cursor = "pointer";
    ahit.addEventListener("dblclick", (ev) => {
      ev.stopPropagation();
      _onEdgeDblclick(ref);
    });
    edgeG.appendChild(ahit);
    edgeG.appendChild(_svg("path", {
      d: _edgePath(srcPos.x, srcPos.y, anchorPos.x, anchorPos.y),
      stroke: color, fill: "none", "stroke-linecap": "round",
      "pointer-events": "none",
      ...(isMerge
        ? { "stroke-width": 2.4, opacity: 1,
            class: "history-edge merge-edge" }
        : { "stroke-width": 1.6, "stroke-dasharray": "4 4", opacity: 0.9,
            class: "history-edge attach-edge" }),
    }));
  });
}
