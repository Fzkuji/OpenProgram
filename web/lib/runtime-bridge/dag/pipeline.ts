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
import { drawNodes } from "./render/nodes";
import { drawBadges } from "./render/badges";
import { drawEdges } from "./render/edges";
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
      m.id + ":" + (m.predecessor || "") + ":" + (m.role || "") + ":" + (m.display || ""),
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
    const owner = isRunNode ? rootId : (rootNode.predecessor || null);
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
    const pid = (tree.byId[nid] as { predecessor?: string }).predecessor
      || (tree.byId[nid] as { predecessor?: string }).predecessor;
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
  // 节点保持原始像素大小（1:1，不缩放）：SVG 画布用内容实际尺寸，宽内容
  // 靠容器 overflow-x 横向滚动查看，而不是把整图（连节点一起）缩进侧栏
  // ——那样多分支时 scale 太小、节点糊成一团。内容比容器窄时至少铺满容器。
  const contentWidth = Math.max(right - left, 40);
  const canvasWidth = Math.max(contentWidth, panelW - 4, 40);
  const height = Math.max(
    PAD_Y * 2 + ROW_H * maxDepth + 24,
    maxYpx + ROW_H + PAD_Y,
  );
  const vbHeight = Math.max(height, 40);

  const svg = _svg("svg", {
    class: "history-svg",
    // viewBox starts at ``left`` (negative if d3 produced left-side
    // children) so off-origin geometry stays inside the visible
    // canvas without re-translating every node.
    viewBox: `${left} 0 ${canvasWidth} ${vbHeight}`,
    width: canvasWidth,
    height: vbHeight,
    preserveAspectRatio: "xMinYMin meet",
  });

  const edgeG = _svg("g", { class: "history-edges" });
  const nodeG = _svg("g", { class: "history-nodes" });
  svg.appendChild(edgeG);
  svg.appendChild(nodeG);

  // Column = backend ``_lane`` (already the final column offset for the
  // branch) + ``_tier`` (indent within the branch). The backend packs
  // lanes (annotate_graph): a fork lane starts one column right of the
  // sibling it diverged from. Do NOT recompute offsets here — that
  // double-counts and pushes forks far away.

  // Compact depth mapping: collapse gaps from folded subtrees.
  const _visibleDepths = Array.from(new Set(
    graph.map((n) => typeof n._depth === "number" ? n._depth : 0),
  )).sort((a, b) => a - b);
  const _depthToRow: Record<number, number> = Object.create(null);
  _visibleDepths.forEach((d, i) => { _depthToRow[d] = i; });

  function pos(n: GNode): { x: number; y: number } {
    const tier = typeof n._tier === "number" ? n._tier : 0;
    const laneCol = n._lane || 0;
    const d = typeof n._depth === "number" ? n._depth : 0;
    const row = _depthToRow[d] ?? d;
    return {
      x: PAD_X + (laneCol + tier) * COL_W,
      y: PAD_Y + row * ROW_H,
    };
  }

  drawEdges(edgeG, tree, graphIn, pos, stableLeafOfNode);

  drawNodes(nodeG, tree, pos, headId, headAncestors, stableLeafOfNode,
    cinfo, _collapsed, internalSet, internalOwner, _contextSet);

  drawBadges(svg, tree, pos, stableLeafOfNode, HGW.currentSessionId || null);

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
