/**
 * DAG renderer — main render() pipeline.
 *
 * Reads a flat ``GNode[]`` + a HEAD id, runs the pass stack to
 * normalise the graph (collapse runtime pairs, merge tool/run-node
 * pairs, demote decoration cards), computes a depth + lane layout,
 * applies the user/auto collapse, and emits the SVG into
 * ``#historyPanel .history-body``.
 *
 * Pipeline order (mirrors ``passes/`` + ``layout/``):
 *
 *   mergeRuns
 *     → collapseRuntimePairs
 *     → demoteDecorationCards
 *     → (stable leafOfNode snapshot from pre-collapse tree)
 *     → applyCollapse
 *     → buildTree + assignDepth + assignLanes
 *     → emit SVG (edges, attach refs, spawn refs, nodes, branch tags)
 *
 * Module-level state (HEAD, collapsed set, last signature, etc.) lives
 * in ``./store/globals`` so other modules (interaction handlers,
 * visibility loop) can read/write the same singletons without
 * circular imports.
 *
 * Behaviour is identical to the pre-split ``history-graph.ts``; this
 * file is the verbatim ``render()`` function with imports rewired.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */

import {
  type GNode,
  HGW,
  NODE_R,
  PAD_X,
  PAD_Y,
  ROW_H,
  COL_W,
} from "./types";
import {
  _branchColor,
  _buildShapeEl,
  _edgePath,
  _treeEdgePath,
  _shapeFor,
  _svg,
} from "./shapes";
import {
  hideTooltip as _hideTooltip,
  resetTooltip as _resetTooltip,
  showTooltip as _showTooltip,
} from "./tooltip";
import { _collapseRuntimePairs } from "./passes/collapse-runtime-pairs";
import { _mergeRuns } from "./passes/merge-runs";
import { _demoteDecorationCards } from "./passes/demote-decoration-cards";
import { _applyCollapse } from "./passes/apply-collapse";
import { _buildTree } from "./layout/build-tree";
import { _assignDepth } from "./layout/depth";
import { _assignLanes, _headAncestors } from "./layout/assign-lanes";
import {
  _recomputeVisibility,
  _wireChatMutationSync,
  _wireChatScrollSync,
  _wirePanelResize,
} from "./render/visibility";
import { _onEdgeDblclick } from "./render/interaction";
import {
  _collapsed,
  _contextSet,
  _currentHead,
  _lastGraph,
  _lastHeadId,
  _lastSignature,
  setCurrentHead,
  setHeadAncestorSet,
  setInternalOwner,
  setInternalSet,
  setLastSignature,
  setLeafOfNode,
  setParentOf,
  setVisibleIds,
} from "./store/globals";

function _signature(graph: GNode[], headId: string | null): string {
  if (!graph || !graph.length) return "empty|" + (headId || "");
  const parts = graph.map(
    (m) =>
      m.id + ":" + (m.called_by || "") + ":" + (m.role || "") + ":" + (m.display || ""),
  );
  parts.sort();
  return parts.join(",") + "|" + (headId || "");
}

export function render(graphIn: GNode[], headIdIn: string | null): void {
  let graph = graphIn;
  let headId = headIdIn;

  const merged = _mergeRuns(graph, headId);
  graph = merged.graph;
  headId = merged.headId;

  const collapsedR = _collapseRuntimePairs(graph, headId);
  graph = collapsedR.graph;
  headId = collapsedR.headId;

  _demoteDecorationCards(graph);

  // Stable leafOfNode from PRE-collapse graph for colouring. Collapsing
  // removes a leaf, which would otherwise make the spawn node itself
  // become a leaf — and ``_branchColor`` would hash a different id,
  // producing a different colour. Snapshotting here keeps collapsed
  // and expanded states colour-consistent.
  const preCollapseTree = _buildTree(graph);
  const preCollapseLanes = _assignLanes(preCollapseTree.byId, headId);
  const stableLeafOfNode = preCollapseLanes.leafOfNode;

  const cinfo = _applyCollapse(graph);
  graph = cinfo.visible;

  const sig = _signature(graph, headId);
  if (sig === _lastSignature && _currentHead === headId) return;
  setLastSignature(sig);
  setCurrentHead(headId);

  const panel = document.getElementById("historyPanel");
  if (!panel) return;
  const body = panel.querySelector(".history-body") as HTMLElement | null;
  if (!body) return;

  if (!graph || !graph.length) {
    const empty = document.createElement("div");
    empty.className = "history-empty";
    empty.textContent = "No messages yet.";
    body.replaceChildren(empty);
    _resetTooltip();
    setLeafOfNode(Object.create(null));
    return;
  }

  const tree = _buildTree(graph);
  const maxDepth = _assignDepth(graph, tree.byId);
  const lanes = _assignLanes(tree.byId, headId);
  setLeafOfNode(lanes.leafOfNode);

  const _colorMap: Record<string, string> = Object.create(null);
  Object.keys(tree.byId).forEach((id) => {
    const node = tree.byId[id];
    if (node._lane !== undefined) {
      _colorMap[id] = _branchColor(node, stableLeafOfNode);
    }
  });
  HGW._branchLaneColorMap = _colorMap;

  const headAncestors: Record<string, boolean> = Object.create(null);
  _headAncestors(tree.byId, headId).forEach((id) => {
    headAncestors[id] = true;
  });
  setHeadAncestorSet(headAncestors);

  const internalSet: Record<string, boolean> = Object.create(null);
  const internalOwner: Record<string, string> = Object.create(null);
  Object.keys(tree.byId).forEach((rootId) => {
    const rootNode = tree.byId[rootId];
    const isRunNode = !!rootNode._runNode;
    if (rootNode.role !== "tool" && !isRunNode) return;
    const owner = isRunNode ? rootId : (rootNode.called_by || null);
    const stack: string[] = [];
    if (isRunNode) {
      (rootNode.children || []).forEach((c) => {
        if (c._internal) stack.push(c.id);
      });
    } else {
      stack.push(rootId);
    }
    while (stack.length) {
      const cur = stack.pop()!;
      internalSet[cur] = true;
      if (owner) internalOwner[cur] = owner;
      const kids = tree.byId[cur].children || [];
      for (let ki = 0; ki < kids.length; ki++) {
        if (isRunNode && !kids[ki]._internal) continue;
        stack.push(kids[ki].id);
      }
    }
  });
  Object.keys(tree.byId).forEach((nid) => {
    const n = tree.byId[nid];
    const c = (n as { caller?: string }).caller;
    if (c && c !== "ROOT") {
      const parent = tree.byId[c];
      if (parent && parent.display === "root") {
        // ROOT's direct children are trunk nodes, not internal
      } else if (parent) {
        internalSet[nid] = true;
        if (!internalOwner[nid]) internalOwner[nid] = c;
      }
    }
  });
  setInternalSet(internalSet);
  setInternalOwner(internalOwner);

  const parentOf: Record<string, string> = Object.create(null);
  Object.keys(tree.byId).forEach((nid) => {
    const pid = (tree.byId[nid] as { called_by?: string; called_by?: string }).called_by
      || (tree.byId[nid] as { called_by?: string }).called_by;
    if (pid) parentOf[nid] = pid;
  });
  setParentOf(parentOf);

  const laneArea = PAD_X + COL_W * Math.max(lanes.laneCount - 1, 0);
  let maxTier = 0;
  Object.keys(tree.byId).forEach((id) => {
    const t = tree.byId[id]._tier;
    if (typeof t === "number" && t > maxTier) maxTier = t;
  });
  const subForkMargin = maxTier >= 1
    ? COL_W * 0.7 + Math.max(0, maxTier - 1) * COL_W * 0.5 + NODE_R * 2
    : 0;
  const panelW = (body && body.clientWidth) || 240;
  // In d3 layout mode the effective node positions live in ``_x`` /
  // ``_y`` and can extend past (or before) what the legacy lane/tier
  // formula projects. Scan the actual coords so the SVG canvas is
  // sized to fit every node and the viewBox can shift if d3 produced
  // a negative x (the left half of a symmetric subtree spread).
  let minX = 0;
  let maxX = 0;
  let maxYpx = 0;
  Object.keys(tree.byId).forEach((id) => {
    const n = tree.byId[id];
    let x: number;
    let y: number;
    if (typeof n._x === "number" && typeof n._y === "number") {
      x = n._x;
      y = n._y;
    } else {
      const t = typeof n._tier === "number" ? n._tier : 0;
      x = PAD_X + (n._lane || 0) * COL_W + t * COL_W;
      y = PAD_Y + (n._depth || 0) * ROW_H;
    }
    if (x < minX) minX = x;
    if (x > maxX) maxX = x;
    if (y > maxYpx) maxYpx = y;
  });
  // Pad both ends so node shapes (radius NODE_R) don't clip.
  const xPad = NODE_R + 4;
  const left = Math.min(0, minX - xPad);
  const right = Math.max(
    laneArea + subForkMargin + PAD_X,
    maxX + xPad,
  );
  const width = Math.max(panelW - 4, right - left);
  const height = Math.max(
    PAD_Y * 2 + ROW_H * maxDepth + 24,
    maxYpx + ROW_H + PAD_Y,
  );

  const svg = _svg("svg", {
    class: "history-svg",
    // viewBox starts at ``left`` (negative if d3 produced left-side
    // children) so off-origin geometry stays inside the visible
    // canvas without re-translating every node.
    viewBox: `${left} 0 ${Math.max(width, 40)} ${Math.max(height, 40)}`,
    width: Math.max(width, 40),
    height: Math.max(height, 40),
  });

  const edgeG = _svg("g", { class: "history-edges" });
  const nodeG = _svg("g", { class: "history-nodes" });
  svg.appendChild(edgeG);
  svg.appendChild(nodeG);

  // Compact lane mapping: only visible lanes get column space.
  // Each lane occupies (maxTierInLane + 1) columns width.
  const _visibleLaneTiers: Record<number, number> = Object.create(null);
  graph.forEach((n) => {
    const ln = n._lane || 0;
    const t = typeof n._tier === "number" ? n._tier : 0;
    _visibleLaneTiers[ln] = Math.max(_visibleLaneTiers[ln] || 0, t);
  });
  const _sortedVisibleLanes = Object.keys(_visibleLaneTiers).map(Number).sort((a, b) => a - b);
  const _laneToCol: Record<number, number> = Object.create(null);
  let _col = 0;
  for (let li = 0; li < _sortedVisibleLanes.length; li++) {
    const ln = _sortedVisibleLanes[li];
    _laneToCol[ln] = _col;
    _col += (_visibleLaneTiers[ln] || 0) + 1;
    // Extra column gap before fork lanes for their virtual trunk line
    if (li < _sortedVisibleLanes.length - 1) _col += 1;
  }

  // Compact depth mapping: collapse gaps from folded subtrees.
  const _visibleDepths = Array.from(new Set(
    graph.map((n) => typeof n._depth === "number" ? n._depth : 0),
  )).sort((a, b) => a - b);
  const _depthToRow: Record<number, number> = Object.create(null);
  _visibleDepths.forEach((d, i) => { _depthToRow[d] = i; });

  function pos(n: GNode): { x: number; y: number } {
    const tier = typeof n._tier === "number" ? n._tier : 0;
    const laneCol = _laneToCol[n._lane || 0] || 0;
    const d = typeof n._depth === "number" ? n._depth : 0;
    const row = _depthToRow[d] ?? d;
    return {
      x: PAD_X + (laneCol + tier) * COL_W,
      y: PAD_Y + row * ROW_H,
    };
  }

  // ── Tree-style edges ──
  // For user nodes: connect from ROOT (tier=0 column) — user is
  // conceptually ROOT's child regardless of called_by (conv chain).
  // For other nodes: connect from their called_by parent.
  // Each edge: vertical drop at parent x, horizontal branch to child x.

  // Find ROOT node position
  const rootNode = Object.values(tree.byId).find((n) => n.display === "root");
  const rootPos = rootNode ? pos(rootNode) : null;

  // Build a full id→node map from pre-collapse graph for parent
  // lookup when a called_by parent was collapsed away.
  const fullById: Record<string, GNode> = Object.create(null);
  graphIn.forEach((m) => { fullById[m.id] = m; });

  const forkNodes: string[] = [];
  Object.keys(tree.byId).forEach((id) => {
    const node = tree.byId[id];
    if (node.display === "root") return;
    let pid = node.called_by;
    // If called_by parent was collapsed, walk up to find visible ancestor
    if (pid && !tree.byId[pid]) {
      let cur = pid;
      let hops = 0;
      while (cur && !tree.byId[cur] && hops < 50) {
        const pn = fullById[cur];
        cur = pn ? (pn.called_by || null) : null;
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

    // User nodes connect from their lane's "root" — the first node
    // in that lane (conceptual trunk). Main lane → ROOT node.
    // Fork lane → the fork branch's first user node.
    // All other nodes connect from their called_by parent directly.
    const p = pos(parent);
    const isUserNode = node.role === "user";
    let trunkX = p.x;
    let fromY = p.y;
    if (isUserNode) {
      const myLane = node._lane || 0;
      if (rootPos && myLane === (rootNode?._lane || 0)) {
        // Main lane → connect from ROOT
        trunkX = rootPos.x;
        fromY = rootPos.y;
      } else {
        // Fork lane → use virtual trunk at forkRoot.x - COL_W
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

    // Vertical trunk from parent row to child row
    if (c.y > fromY) {
      edgeG.appendChild(_svg("line", {
        x1: trunkX, y1: fromY, x2: trunkX, y2: c.y,
        stroke: color,
        "stroke-width": 1.6,
        "stroke-linecap": "round",
        "pointer-events": "none",
        class: "history-edge",
      }));
    }
    // Horizontal branch from trunk to child
    if (c.x !== trunkX) {
      edgeG.appendChild(_svg("line", {
        x1: trunkX, y1: c.y, x2: c.x - nr, y2: c.y,
        stroke: color,
        "stroke-width": 1.6,
        "stroke-linecap": "round",
        "pointer-events": "none",
        class: "history-edge",
      }));
    }
  });

  // Fork branches: dashed bridge from main sibling → fork root,
  // then a solid vertical trunk line within the fork lane so that
  // subsequent user nodes branch off it (mirroring main lane's ROOT trunk).
  const forkRoots: Record<number, GNode> = Object.create(null);
  for (const id of forkNodes) {
    const node = tree.byId[id];
    if (!node) continue;
    const myLane = node._lane || 0;
    if (!forkRoots[myLane] || (node._depth || 0) < (forkRoots[myLane]._depth || 0)) {
      forkRoots[myLane] = node;
    }
  }
  // Draw dashed bridge for fork roots only (first node in each fork lane)
  for (const id of forkNodes) {
    const node = tree.byId[id];
    if (!node) continue;
    const myLane = node._lane || 0;
    if (forkRoots[myLane]?.id !== id) continue;
    const pid = node.called_by;
    if (!pid) continue;
    let sibling: GNode | null = null;
    Object.keys(tree.byId).forEach((sid) => {
      if (sid === id) return;
      const sn = tree.byId[sid];
      if (sn.called_by === pid && (sn._lane || 0) !== myLane) {
        if (!sibling) sibling = sn;
      }
    });
    if (!sibling) continue;
    const sp = pos(sibling);
    const forkPos = pos(node);
    const nr = NODE_R + 4;
    const trunkX = forkPos.x - COL_W;
    const color = _branchColor(node, stableLeafOfNode);
    // Dashed bridge: main sibling → fork trunk column
    edgeG.appendChild(_svg("path", {
      d: _edgePath(sp.x + nr, sp.y, trunkX, forkPos.y),
      stroke: color,
      "stroke-width": 1.4,
      fill: "none",
      "stroke-dasharray": "6 4",
      opacity: 0.7,
      "pointer-events": "none",
      class: "history-edge fork-edge",
    }));
    // Solid horizontal branch: trunk → fork root node
    edgeG.appendChild(_svg("line", {
      x1: trunkX, y1: forkPos.y, x2: forkPos.x - nr, y2: forkPos.y,
      stroke: color,
      "stroke-width": 1.6,
      "stroke-linecap": "round",
      "pointer-events": "none",
      class: "history-edge",
    }));
    // Solid vertical trunk for the fork lane (from fork root down
    // to the last user node in this lane — not past the last llm)
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
        stroke: color,
        "stroke-width": 1.6,
        "stroke-linecap": "round",
        "pointer-events": "none",
        class: "history-edge",
      }));
    }
  }

  // Attach-reference edges: dashed line from source branch tip to the
  // attach pointer. Marching-ants animation (CSS) carries direction.
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
      stroke: "transparent",
      "stroke-width": 14,
      fill: "none",
      "pointer-events": "stroke",
      "data-target-id": ref,
      class: "history-edge-hit attach-edge-hit",
    });
    (ahit as SVGGraphicsElement).style.cursor = "pointer";
    ahit.addEventListener("dblclick", (ev) => {
      ev.stopPropagation();
      _onEdgeDblclick(ref);
    });
    edgeG.appendChild(ahit);
    edgeG.appendChild(
      _svg("path", {
        d: _edgePath(srcPos.x, srcPos.y, anchorPos.x, anchorPos.y),
        stroke: color,
        "stroke-width": 1.6,
        fill: "none",
        "stroke-linecap": "round",
        "stroke-dasharray": "4 4",
        opacity: 0.9,
        "pointer-events": "none",
        class: "history-edge attach-edge",
      }),
    );
  });

  // Spawn edges — function_call(task) on caller's lane to the sub-branch root.
  Object.keys(tree.byId).forEach((id) => {
    const taskNode = tree.byId[id];
    if (taskNode.role !== "tool" || taskNode.function !== "task") return;
    const callerId = taskNode.caller || taskNode.called_by || "";
    if (!callerId) return;
    let subTipId = "";
    for (const k of Object.keys(tree.byId)) {
      const n = tree.byId[k];
      if (n.function !== "attach") continue;
      const ac = n.caller || n.called_by || "";
      const ap = n.called_by || "";
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
      const pp: string | null | undefined = nn && (nn.called_by);
      if (!pp || !tree.byId[pp]) break;
      cur = pp;
    }
    const subRoot = cur && tree.byId[cur];
    if (!subRoot) return;
    const srcPos = pos(taskNode);
    const dstPos = pos(subRoot);
    const shit = _svg("path", {
      d: _edgePath(srcPos.x, srcPos.y, dstPos.x, dstPos.y),
      stroke: "transparent",
      "stroke-width": 14,
      fill: "none",
      "pointer-events": "stroke",
      "data-target-id": subRoot.id,
      class: "history-edge-hit spawn-edge-hit",
    });
    (shit as SVGGraphicsElement).style.cursor = "pointer";
    const subRootId = subRoot.id;
    shit.addEventListener("dblclick", (ev) => {
      ev.stopPropagation();
      _onEdgeDblclick(subRootId);
    });
    edgeG.appendChild(shit);
    edgeG.appendChild(
      _svg("path", {
        d: _edgePath(srcPos.x, srcPos.y, dstPos.x, dstPos.y),
        stroke: "var(--text-muted, #8b8b8b)",
        "stroke-width": 1.2,
        fill: "none",
        "stroke-linecap": "round",
        "stroke-dasharray": "1 4",
        opacity: 0.8,
        "pointer-events": "none",
        class: "history-edge spawn-edge",
      }),
    );
  });

  Object.keys(tree.byId).forEach((id) => {
    const node = tree.byId[id];
    const p = pos(node);
    const isHead = id === headId;
    const onHead = !!headAncestors[id];
    const color = _branchColor(node, stableLeafOfNode);
    const isCollapsible = cinfo.isCollapsible(node);
    const folded = isCollapsible && !!_collapsed[id];
    const isBranchOp = node.function === "task"
      || node.function === "attach"
      || node.function === "merge";
    const oocFlag = _contextSet && !_contextSet[id] && !isBranchOp;
    const g = _svg("g", {
      class:
        "history-node" +
        (isHead ? " is-head" : "") +
        (onHead ? "" : " off-head") +
        (isCollapsible ? " is-collapsible" : "") +
        (oocFlag ? " out-of-context" : ""),
      transform: "translate(" + p.x + "," + p.y + ")",
      "data-msg-id": id,
      "data-collapsible": isCollapsible ? "1" : "0",
      "data-collapsed": folded ? "1" : "0",
      "data-internal": internalSet[id] ? "1" : "0",
      "data-owner": internalOwner[id] || "",
    });
    const hit = _svg("circle", {
      r: "7",
      fill: "transparent",
      "pointer-events": "all",
    });
    g.appendChild(hit);
    (g as SVGGraphicsElement).style.cursor = "pointer";
    // Uniform node radius — older code shrank by tier (deeper caller
    // chain = smaller dot) and by onHead status. That left top-of-DAG
    // function-call squares hard to see whenever the chat had
    // scrolled away. Keep one consistent size for all nodes; the
    // "currently in chat viewport" cue is shown via the white inner
    // fill overlay instead.
    const r = NODE_R + 1.8;
    const el = _buildShapeEl(_shapeFor(node), color, r);
    if (el) {
      el.setAttribute("pointer-events", "none");
      g.appendChild(el);
    }
    if (isCollapsible) {
      const hc = cinfo.hiddenCount[id] || 0;
      const badge = _svg("text", {
        x: String(NODE_R + 3),
        y: String(NODE_R + 5),
        class: "history-fold-badge",
        "pointer-events": "none",
      });
      badge.textContent = folded ? "+" + hc : "−";
      g.appendChild(badge);
    }
    (g as any)._nodeData = node;
    nodeG.appendChild(g);
  });

  (function _drawBranchBadges() {
    const sid = HGW.currentSessionId;
    const rows = (sid && HGW._branchesByConv && HGW._branchesByConv[sid]) || [];
    if (!rows.length) return;
    const tagG = _svg("g", { class: "history-branch-tags" });
    rows.forEach((b) => {
      const node = b.head_msg_id ? tree.byId[b.head_msg_id] : null;
      if (!node) return;
      const p = pos(node);
      const label = (b.name as string) || b.head_msg_id.slice(0, 8);
      const isActive = !!b.active;
      const dy = 28;
      const color = _branchColor(node, stableLeafOfNode);
      const tg = _svg("g", {
        class: "history-branch-tag" + (isActive ? " active" : ""),
        transform: "translate(" + p.x + "," + (p.y + dy) + ")",
        "data-head": b.head_msg_id,
      });
      (tg as SVGGraphicsElement).style.cursor = isActive ? "default" : "pointer";
      const bw = Math.max(label.length * 6 + 12, 40);
      const bh = 18;
      const rect = _svg("rect", {
        x: String(-bw / 2),
        y: String(-bh / 2),
        width: String(bw),
        height: String(bh),
        rx: "6",
        ry: "6",
        fill: "var(--bg-hover, #2e2e2c)",
        opacity: "0.85",
      });
      tg.appendChild(rect);
      const text = _svg("text", {
        x: "0",
        y: "0",
        "text-anchor": "middle",
        "dominant-baseline": "central",
        "font-size": "9",
        "font-family": "var(--font-sans, sans-serif)",
        "font-weight": "500",
        fill: isActive ? "var(--text-bright, #f8f8f6)" : "var(--text-muted, #6b6a63)",
        "pointer-events": "none",
      });
      text.textContent = label;
      tg.appendChild(text);
      if (!isActive) {
        tg.addEventListener("click", (ev) => {
          ev.stopPropagation();
          if (HGW.ws && HGW.ws.readyState === WebSocket.OPEN) {
            HGW.ws.send(JSON.stringify({
              action: "checkout_branch",
              session_id: sid,
              head_msg_id: b.head_msg_id,
            }));
            HGW.ws.send(JSON.stringify({
              action: "load_session",
              session_id: sid,
            }));
          }
        });
      }
      tagG.appendChild(tg);
    });
    svg.appendChild(tagG);
  })();

  body.replaceChildren(svg);
  _resetTooltip();
  setVisibleIds(Object.create(null));

  _wireChatScrollSync();
  _wireChatMutationSync();
  _wirePanelResize(() => {
    if (!_lastGraph) return;
    setLastSignature(null);
    render(_lastGraph, _lastHeadId);
  });
  _recomputeVisibility();
  requestAnimationFrame(_recomputeVisibility);
  setTimeout(_recomputeVisibility, 250);
  setTimeout(_recomputeVisibility, 700);

  const bodyAny = body as any;
  if (!bodyAny._historyHoverWired) {
    bodyAny._historyHoverWired = true;
    let _activeNode: any = null;
    body.addEventListener("mouseover", (e: MouseEvent) => {
      const tgt = e.target as HTMLElement;
      const g = tgt.closest && (tgt.closest(".history-node") as any);
      if (!g || !g._nodeData) return;
      if (g === _activeNode) return;
      _activeNode = g;
      _showTooltip(body, g._nodeData, g.getBoundingClientRect());
    });
    body.addEventListener("mouseout", (e: MouseEvent) => {
      const tgt = e.target as HTMLElement;
      const g = tgt.closest && (tgt.closest(".history-node") as any);
      if (!g) return;
      const rel = e.relatedTarget as HTMLElement | null;
      if (rel && rel.closest && rel.closest(".history-node") === g) return;
      if (_activeNode === g) {
        _activeNode = null;
        _hideTooltip();
      }
    });
    body.addEventListener("mouseleave", () => {
      _activeNode = null;
      _hideTooltip();
    });
  }
}
