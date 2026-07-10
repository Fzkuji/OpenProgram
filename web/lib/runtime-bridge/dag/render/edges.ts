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
    // spawn 分支根的连线是点划线 spawn 边（下方专属段），不走对话链 /
    // fork 桥接，否则同一对节点会叠两种线。
    if ((node as Record<string, unknown>).source === "agent_spawn"
        && !node.predecessor) {
      return;
    }
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
    // 分叉点：对话前驱优先，没有则用 caller（每轮/每分支的首节点靠
    // caller="ROOT" 挂根、predecessor 为空）。只认 predecessor 会让这些
    // 从 ROOT 分出的兄弟分支画不出 fork 桥接线、看着悬空脱离主干。
    const pid = node.predecessor || node.caller;
    if (!pid) continue;
    // 兄弟分支的首节点之间用虚线**逐级串联**：选同一分叉点(同 pid)下、
    // lane 比自己小且最接近的那个分支根作为 sibling，虚线从它连到本分支。
    // 于是分支1→分支2→分支3 依次串起来（对齐用户期望的 fork 连法），而不是
    // 所有分支都连回同一个基准节点。
    let sibling: GNode | null = null;
    let siblingLane = -1;
    Object.keys(tree.byId).forEach((sid) => {
      if (sid === id) return;
      const sn = tree.byId[sid];
      const spid = sn.predecessor || sn.caller;
      const snLane = sn._lane || 0;
      // 只在同分叉点的分支根之间连；取 lane < myLane 里最大的（紧邻前驱）。
      if (spid === pid && snLane < myLane && (forkRoots[snLane]?.id === sid || snLane === 0)) {
        if (snLane > siblingLane) {
          siblingLane = snLane;
          sibling = sn;
        }
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

  // ── Attach / merge reference edges ──
  // attach 指针节点不画（后端随 display=runtime 过滤，dag-rendering.md
  // 场景 8/10），它的 ref 由 graph_builder 戳在嵌入位置节点的
  // ``attach_returns`` 上——回流长虚线从子分支 tip 画回嵌入位置。merge
  // 节点在 tree 里（◉），汇入线按 peer 分支色加粗实线（场景 8）。
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

  // ── Spawn edges ──
  // spawn 根直接带 caller=发起节点，点划线（4 2 1 2）按子分支色（场景
  // 10）。发起节点折叠在 ⚒N 里时，沿 caller 链上溯到第一个可见节点
  // （通常是那轮的 llm）作为线的起点。
  Object.keys(tree.byId).forEach((id) => {
    const subRoot = tree.byId[id];
    if ((subRoot as Record<string, unknown>).source !== "agent_spawn") return;
    if (subRoot.predecessor) return;
    let srcId: string | undefined = subRoot.caller as string | undefined;
    let hops = 0;
    while (srcId && !tree.byId[srcId] && hops < 50) {
      const sn = fullById[srcId];
      srcId = sn ? (sn.caller || sn.predecessor || undefined) : undefined;
      hops++;
    }
    if (!srcId || !tree.byId[srcId]) return;
    const srcNode = tree.byId[srcId];
    if (srcNode.display === "root") return;
    const srcPos = pos(srcNode);
    const dstPos = pos(subRoot);
    const color = _branchColor(subRoot, stableLeafOfNode);
    const hitPath = _svg("path", {
      d: _edgePath(srcPos.x, srcPos.y, dstPos.x, dstPos.y),
      stroke: "transparent", "stroke-width": 14, fill: "none",
      "pointer-events": "stroke", "data-target-id": id,
      class: "history-edge-hit spawn-edge-hit",
    });
    (hitPath as SVGGraphicsElement).style.cursor = "pointer";
    hitPath.addEventListener("dblclick", (ev) => {
      ev.stopPropagation();
      _onEdgeDblclick(id);
    });
    edgeG.appendChild(hitPath);
    edgeG.appendChild(_svg("path", {
      d: _edgePath(srcPos.x, srcPos.y, dstPos.x, dstPos.y),
      stroke: color, "stroke-width": 1.4,
      fill: "none", "stroke-linecap": "round",
      "stroke-dasharray": "4 2 1 2", opacity: 0.85,
      "pointer-events": "none", class: "history-edge spawn-edge",
    }));
  });
}
