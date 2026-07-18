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
} from "./types";
import { computeGeometry } from "./layout/geometry";
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

// Which session the panel currently shows (undefined = never rendered).
// First render after a session switch gets the ``dag-enter`` fade-in;
// subsequent re-renders of the same session swap in place, no flash.
let _lastRenderedSession: string | null | undefined;

/** Session switch, no capture yet: replace the DAG with pulsing
 *  placeholder bars so the previous session's graph doesn't linger.
 *  Must bust the signature dedup — otherwise the next render() of an
 *  identical graph early-returns and the skeleton never goes away. */
export function showHistorySkeleton(): void {
  const panel = document.getElementById("historyPanel");
  const body = panel && (panel.querySelector(".history-body") as HTMLElement | null);
  if (!body) return;
  setLastSignature(null);
  _lastRenderedSession = "__loading__";
  const el = document.createElement("div");
  el.className = "history-skeleton";
  for (const w of [70, 52, 61]) {
    const bar = document.createElement("div");
    bar.className = "history-skeleton-bar";
    bar.style.width = w + "%";
    el.appendChild(bar);
  }
  body.replaceChildren(el);
}

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

  // attach 指针节点不画（dag-rendering.md 场景 8/10）：它是"head 在哪"
  // 的数据锚点，留在对话链尾，viewport 里只画回流虚线，不占格。只过滤
  // 链尾指针（无对话后继）；head 指着它时把 head 退回它的前驱。
  {
    const hasConvChild: Record<string, boolean> = Object.create(null);
    graph.forEach((m) => {
      if (m.predecessor) hasConvChild[m.predecessor] = true;
    });
    const attachTail = (m: GNode): boolean =>
      m.function === "attach" && !hasConvChild[m.id];
    const dropped = graph.filter(attachTail);
    if (dropped.length) {
      graph = graph.filter((m) => !attachTail(m));
      const droppedHead = dropped.find((m) => m.id === headId);
      if (droppedHead) headId = droppedHead.predecessor || headId;
    }
  }

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
    _lastRenderedSession = (HGW.currentSessionId as string | undefined) || null;
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
    // spawn 分支根是对话层节点：caller 只是记录谁发起了它，不代表它是
    // 发起轮的执行内部节点（否则点击变成"滚到 owner"而不是 checkout）。
    if ((n as Record<string, unknown>).source === "agent_spawn" && !n.predecessor) {
      return;
    }
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

  const panelW = (body && body.clientWidth) || 240;
  // Content-driven pixel packing: lane columns sized to the widest
  // *visible* tier in each lane (collapse a branch → its neighbours pack
  // back), and per-lane rows so call-tree siblings never overlap. See
  // ``layout/geometry.ts``.
  const geom = computeGeometry(tree.byId);
  const minX = geom.minX;
  const maxX = geom.maxX;
  const maxYpx = geom.maxY;
  // Pad both ends so node shapes (radius NODE_R) don't clip.
  const xPad = NODE_R + 4;
  const left = Math.min(0, minX - xPad);
  const right = maxX + xPad + PAD_X;
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

  // Positions come from ``computeGeometry`` (content-driven lane packing
  // + per-lane row de-collision). ``pos`` is a thin lookup so the edge /
  // node / badge drawers share one source of truth.
  function pos(n: GNode): { x: number; y: number } {
    return geom.pos[n.id] || { x: PAD_X, y: PAD_Y };
  }

  drawEdges(edgeG, tree, graphIn, pos, stableLeafOfNode);

  drawNodes(nodeG, tree, pos, headId, headAncestors, stableLeafOfNode,
    cinfo, _collapsed, internalSet, internalOwner, _contextSet);

  const fullById: Record<string, GNode> = Object.create(null);
  graphIn.forEach((m) => { fullById[m.id] = m; });
  drawBadges(svg, tree, pos, stableLeafOfNode, HGW.currentSessionId || null,
    fullById);

  // 会话切换后的首次绘制淡入（配合 transcript 的 session-enter），
  // 同会话的增量重绘原地替换，不闪。
  const sess = (HGW.currentSessionId as string | undefined) || null;
  if (_lastRenderedSession !== sess) svg.classList.add("dag-enter");
  _lastRenderedSession = sess;

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
