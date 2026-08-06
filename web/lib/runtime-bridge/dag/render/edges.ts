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
import { _branchColor, _edgePath, _svg } from "../shapes";
import { _onEdgeDblclick } from "./interaction";
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
  const ghostIds: Record<string, boolean> = Object.create(null);
  for (const n of graphIn) {
    const ids = coversIds(n);
    if (!ids) continue;
    for (const cid of ids) ghostIds[cid] = true;
  }
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
      return;
    }
    const c = pos(node);
    const isGhost = !!ghostIds[id];
    const color = isGhost ? GHOST_STROKE : _branchColor(node, stableLeafOfNode);
    const dash: Record<string, string> = isGhost
      ? { "stroke-dasharray": "3 3" }
      : {};

    const p = pos(parent);
    const isUserNode = node.role === "user";
    let trunkX = p.x;
    let fromY = p.y;
    if (isUserNode) {
      const myLane = node._lane || 0;
      if (rootPos && myLane === (rootNode?._lane || 0)) {
        trunkX = rootPos.x;
        fromY = rootPos.y;
      } else {
        let forkRootNode: GNode | null = null;
        Object.values(tree.byId).forEach((n) => {
          if ((n._lane || 0) !== myLane) return;
          if (!forkRootNode || (n._depth || 0) < (forkRootNode._depth || 0)) {
            forkRootNode = n;
          }
        });
        if (forkRootNode) {
          const fp = pos(forkRootNode);
          trunkX = fp.x;
          fromY = fp.y;
        } else {
          trunkX = c.x;
        }
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
    if (sib && pos(sib).y === d.y) {
      const sp = pos(sib);
      edgeG.appendChild(_svg("line", {
        x1: sp.x, y1: d.y, x2: d.x, y2: d.y,
        stroke: color, "stroke-width": 1.5, "stroke-linecap": "round",
        "stroke-dasharray": "6 4", opacity: 0.7,
        "pointer-events": "none", class: "history-edge fork-edge",
      }));
      continue;
    }
    // Elbow fallback — from the fork point itself. Reached when a later
    // pass has moved one of the two off the shared row (a thread pushing
    // rows down, say); siblings that stayed level take the bridge above.
    let fp = node.predecessor || node.caller || "";
    let fhops = 0;
    while (fp && !tree.byId[fp] && fhops < 50) {
      const fn = fullById[fp];
      fp = fn ? (fn.predecessor || fn.caller || "") : "";
      fhops++;
    }
    const fpNode = fp ? tree.byId[fp] : undefined;
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
        + `L ${vx} ${d.y - r} Q ${vx} ${d.y} ${vx + r} ${d.y} `
        + `L ${d.x} ${d.y}`,
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
    edgeG.appendChild(_svg("path", {
      d: `M ${ap.x} ${ap.y} Q ${tx} ${ap.y} ${tx} ${ap.y + 22} `
        + `L ${tx} ${lastY}`,
      stroke: GHOST_STROKE, "stroke-width": 1.3, fill: "none",
      "stroke-dasharray": "2 3", "stroke-linecap": "round",
      opacity: 0.8,
      "pointer-events": "none", class: "history-edge thread-edge",
    }));
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
