/**
 * Renderer: edge SVG drawing.
 *
 * Four edge kinds:
 *   * conv chain — solid coloured, vertical trunk + horizontal branch.
 *   * fork bridge — dashed marching-ants from main sibling to fork trunk.
 *   * attach_ref — dashed marching-ants from source branch tip to attach node.
 *   * spawn — dot-dash grey from task node to sub-branch root.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */

import { type GNode, NODE_R, COL_W } from "../types";
import { _branchColor, _edgePath, _svg } from "../shapes";
import { _onEdgeDblclick } from "./interaction";

export function drawEdges(
  edgeG: SVGElement,
  tree: { byId: Record<string, GNode> },
  graphIn: GNode[],
  pos: (n: GNode) => { x: number; y: number },
  stableLeafOfNode: Record<string, string>,
): void {
  const rootNode = Object.values(tree.byId).find((n) => n.display === "root");
  const rootPos = rootNode ? pos(rootNode) : null;

  const fullById: Record<string, GNode> = Object.create(null);
  graphIn.forEach((m) => { fullById[m.id] = m; });

  const forkNodes: string[] = [];

  // ── Conv-chain edges ──
  Object.keys(tree.byId).forEach((id) => {
    const node = tree.byId[id];
    if (node.display === "root") return;
    // Parent edge: predecessor (conv chain) if present, else caller
    // (sub-call). A first user / a tool has no predecessor — its parent
    // is its caller (ROOT / the llm), so the edge must follow caller.
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
    const color = _branchColor(node, stableLeafOfNode);
    const nr = NODE_R + 4;

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
          trunkX = fp.x - COL_W;
          fromY = fp.y;
        } else {
          trunkX = c.x;
        }
      }
    }

    // Start the vertical trunk at the parent's BOTTOM edge, not its
    // centre — otherwise the line runs through the lower half of the
    // parent node. Only when the trunk sits in the parent's own column
    // (sub-call indent) would it overlap; offsetting by nr is harmless
    // when the trunk is a separate column (user → ROOT lane).
    const vTop = fromY + nr;
    if (c.y > vTop) {
      edgeG.appendChild(_svg("line", {
        x1: trunkX, y1: vTop, x2: trunkX, y2: c.y,
        stroke: color, "stroke-width": 1.6, "stroke-linecap": "round",
        "pointer-events": "none", class: "history-edge",
      }));
    }
    if (c.x !== trunkX) {
      edgeG.appendChild(_svg("line", {
        x1: trunkX, y1: c.y, x2: c.x - nr, y2: c.y,
        stroke: color, "stroke-width": 1.6, "stroke-linecap": "round",
        "pointer-events": "none", class: "history-edge",
      }));
    }
  });

  // ── Fork bridges + fork trunks ──
  const forkRoots: Record<number, GNode> = Object.create(null);
  for (const id of forkNodes) {
    const node = tree.byId[id];
    if (!node) continue;
    const myLane = node._lane || 0;
    if (!forkRoots[myLane] || (node._depth || 0) < (forkRoots[myLane]._depth || 0)) {
      forkRoots[myLane] = node;
    }
  }
  for (const id of forkNodes) {
    const node = tree.byId[id];
    if (!node) continue;
    const myLane = node._lane || 0;
    if (forkRoots[myLane]?.id !== id) continue;
    const pid = node.predecessor;
    if (!pid) continue;
    let sibling: GNode | null = null;
    Object.keys(tree.byId).forEach((sid) => {
      if (sid === id) return;
      const sn = tree.byId[sid];
      if (sn.predecessor === pid && (sn._lane || 0) !== myLane) {
        if (!sibling) sibling = sn;
      }
    });
    if (!sibling) continue;
    const sp = pos(sibling);
    const forkPos = pos(node);
    const nr = NODE_R + 4;
    const trunkX = forkPos.x - COL_W;
    const color = _branchColor(node, stableLeafOfNode);
    edgeG.appendChild(_svg("path", {
      d: _edgePath(sp.x + nr, sp.y, trunkX, forkPos.y),
      stroke: color, "stroke-width": 1.4, fill: "none",
      "stroke-dasharray": "6 4", opacity: 0.7,
      "pointer-events": "none", class: "history-edge fork-edge",
    }));
    edgeG.appendChild(_svg("line", {
      x1: trunkX, y1: forkPos.y, x2: forkPos.x - nr, y2: forkPos.y,
      stroke: color, "stroke-width": 1.6, "stroke-linecap": "round",
      "pointer-events": "none", class: "history-edge",
    }));
    let lastY = forkPos.y;
    Object.values(tree.byId).forEach((n) => {
      if ((n._lane || 0) !== myLane) return;
      if (n.role !== "user") return;
      const np = pos(n);
      if (np.y > lastY) lastY = np.y;
    });
    if (lastY > forkPos.y) {
      edgeG.appendChild(_svg("line", {
        x1: trunkX, y1: forkPos.y, x2: trunkX, y2: lastY,
        stroke: color, "stroke-width": 1.6, "stroke-linecap": "round",
        "pointer-events": "none", class: "history-edge",
      }));
    }
  }

  // ── Attach-reference edges ──
  Object.keys(tree.byId).forEach((id) => {
    const node = tree.byId[id];
    if (node.function !== "attach" && node.function !== "merge") return;
    const ref = node.attach_ref as string | undefined;
    if (!ref) return;
    const src = tree.byId[ref];
    if (!src) return;
    const srcPos = pos(src);
    const anchorPos = pos(node);
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
      stroke: color, "stroke-width": 1.6, fill: "none",
      "stroke-linecap": "round", "stroke-dasharray": "4 4", opacity: 0.9,
      "pointer-events": "none", class: "history-edge attach-edge",
    }));
  });

  // ── Spawn edges ──
  Object.keys(tree.byId).forEach((id) => {
    const taskNode = tree.byId[id];
    if (taskNode.role !== "tool" || taskNode.function !== "task") return;
    const callerId = taskNode.caller || taskNode.predecessor || "";
    if (!callerId) return;
    let subTipId = "";
    for (const k of Object.keys(tree.byId)) {
      const n = tree.byId[k];
      if (n.function !== "attach") continue;
      const ac = n.caller || n.predecessor || "";
      const ap = n.predecessor || "";
      if (ac === callerId || ap === callerId) {
        subTipId = String(n.attach_ref || "");
        break;
      }
    }
    if (!subTipId || !tree.byId[subTipId]) return;
    let cur: string | undefined = subTipId;
    const seen: Record<string, boolean> = Object.create(null);
    while (cur && !seen[cur]) {
      seen[cur] = true;
      const nn: GNode | undefined = tree.byId[cur];
      const pp: string | null | undefined = nn && (nn.predecessor);
      if (!pp || !tree.byId[pp]) break;
      cur = pp;
    }
    const subRoot = cur && tree.byId[cur];
    if (!subRoot) return;
    const srcPos = pos(taskNode);
    const dstPos = pos(subRoot);
    const hitPath = _svg("path", {
      d: _edgePath(srcPos.x, srcPos.y, dstPos.x, dstPos.y),
      stroke: "transparent", "stroke-width": 14, fill: "none",
      "pointer-events": "stroke", "data-target-id": subRoot.id,
      class: "history-edge-hit spawn-edge-hit",
    });
    (hitPath as SVGGraphicsElement).style.cursor = "pointer";
    const subRootId = subRoot.id;
    hitPath.addEventListener("dblclick", (ev) => {
      ev.stopPropagation();
      _onEdgeDblclick(subRootId);
    });
    edgeG.appendChild(hitPath);
    edgeG.appendChild(_svg("path", {
      d: _edgePath(srcPos.x, srcPos.y, dstPos.x, dstPos.y),
      stroke: "var(--text-muted, #8b8b8b)", "stroke-width": 1.2,
      fill: "none", "stroke-linecap": "round",
      "stroke-dasharray": "1 4", opacity: 0.8,
      "pointer-events": "none", class: "history-edge spawn-edge",
    }));
  });
}
