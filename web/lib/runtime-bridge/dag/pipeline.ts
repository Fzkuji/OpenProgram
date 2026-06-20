/**
 * DAG renderer — main render() pipeline.
 *
 * Reads a flat ``GNode[]`` + a HEAD id, runs the pass stack to
 * normalise the graph (collapse runtime pairs, merge tool/run-node
 * pairs, collapse runtime placeholders, demote decoration cards),
 * computes a depth + lane layout, applies the user/auto collapse,
 * and emits the SVG into ``#historyPanel .history-body``.
 *
 * Pipeline order (mirrors ``passes/`` + ``layout/``):
 *
 *   mergeRuns
 *     → collapseRuntimePairs
 *     → collapseRuntimePlaceholders
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
import { _collapseRuntimePlaceholders } from "./passes/collapse-runtime-placeholders";
import { _demoteDecorationCards } from "./passes/demote-decoration-cards";
import { _applyCollapse } from "./passes/apply-collapse";
import { _applyD3TreeLayout } from "./passes/d3-tree-layout";
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
  _layoutMode,
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

  // collapseRuntimePlaceholders MUST run first. It folds the
  // ``runtime placeholder + same-named code call`` pair into a single
  // surviving code node, with the placeholder's old conv-children
  // (e.g. a follow-up user msg whose parent_id pointed at the
  // placeholder) reparented onto the surviving code.
  //
  // Without this ordering, ``_mergeRuns`` would see the placeholder
  // still in place with two kids — the code (tool) + the followup
  // user msg — treat them as ``wrapper + run-output``, and merge the
  // code's caller-tree onto the user msg. That visually attaches
  // ``gui_step`` + ``conclusion`` to the user dot in mini-DAG.
  const collapsedRP = _collapseRuntimePlaceholders(graph, headId);
  graph = collapsedRP.graph;
  headId = collapsedRP.headId;

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

  // d3-tree layout mode: re-position function-call subtrees via
  // d3-hierarchy's Reingold-Tilford layout so siblings spread to
  // accommodate their own subtree widths (no overlap on complex
  // agentic call trees). Trunk (user/reply chain) stays on its
  // legacy lane=0 column. Mode flip is reactive — toggling at
  // runtime re-renders with the new positions.
  if (_layoutMode === "d3") {
    _applyD3TreeLayout(graph);
  } else {
    // Clear any d3 coords from a previous run when switching back
    // to legacy, so ``pos()`` falls through to the lane/tier formula.
    graph.forEach((n) => {
      if ("_x" in n) delete (n as { _x?: number })._x;
      if ("_y" in n) delete (n as { _y?: number })._y;
    });
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
    const c = (n as { caller?: string; called_by?: string }).caller
      || (n as { called_by?: string }).called_by;
    if (c) {
      internalSet[nid] = true;
      if (!internalOwner[nid]) internalOwner[nid] = c;
    }
  });
  setInternalSet(internalSet);
  setInternalOwner(internalOwner);

  const parentOf: Record<string, string> = Object.create(null);
  Object.keys(tree.byId).forEach((nid) => {
    const pid = (tree.byId[nid] as { called_by?: string; parent_id?: string }).called_by
      || (tree.byId[nid] as { parent_id?: string }).parent_id;
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

  function pos(n: GNode): { x: number; y: number } {
    // d3-tree layout (if active) writes absolute pixel positions to
    // ``_x`` / ``_y`` for function-call subtree nodes. Trunk nodes
    // keep the legacy lane/tier/depth formula. Either way the
    // ``pos()`` API returns one resolved (x, y) per node.
    if (typeof n._x === "number" && typeof n._y === "number") {
      return { x: n._x, y: n._y };
    }
    const tier = typeof n._tier === "number" ? n._tier : 0;
    const tierOff = tier * COL_W;
    return {
      x: PAD_X + (n._lane || 0) * COL_W + tierOff,
      y: PAD_Y + (n._depth || 0) * ROW_H,
    };
  }

  // ── Tree-style edges: vertical trunk lines + horizontal branches ──
  // For each parent with called_by children on the same lane: draw ONE
  // continuous vertical line from the parent down to the last child's
  // row, then a short horizontal line from the trunk to each child.
  // Fork siblings (different lane, no called_by) get a horizontal
  // dashed line to their first sibling.

  // Group children by their called_by parent (same lane only)
  const trunkChildren: Record<string, string[]> = Object.create(null);
  const forkNodes: string[] = [];
  Object.keys(tree.byId).forEach((id) => {
    const node = tree.byId[id];
    const parentId = node.called_by;
    if (parentId && tree.byId[parentId]) {
      const parent = tree.byId[parentId];
      if ((node._lane || 0) === (parent._lane || 0)) {
        (trunkChildren[parentId] = trunkChildren[parentId] || []).push(id);
        return;
      }
    }
    // No called_by or different lane = fork branch
    if (!parentId && node.parent_id) {
      forkNodes.push(id);
    }
  });

  // Draw trunk lines + horizontal branches
  Object.keys(trunkChildren).forEach((parentId) => {
    const parent = tree.byId[parentId];
    if (!parent) return;
    const kids = trunkChildren[parentId];
    const p = pos(parent);
    const color = _branchColor(parent, stableLeafOfNode);
    const nr = NODE_R + 4;

    // Find the last child's y position for the vertical trunk
    let lastY = p.y;
    for (const kid of kids) {
      const kp = pos(tree.byId[kid]);
      if (kp.y > lastY) lastY = kp.y;
    }

    // Vertical trunk line from parent down to last child's row
    if (lastY > p.y) {
      edgeG.appendChild(_svg("line", {
        x1: p.x, y1: p.y, x2: p.x, y2: lastY,
        stroke: color,
        "stroke-width": 1.6,
        "stroke-linecap": "round",
        "pointer-events": "none",
        class: "history-edge",
      }));
    }

    // Horizontal branch from trunk to each child
    for (const kid of kids) {
      const kn = tree.byId[kid];
      const kp = pos(kn);
      if (kp.x !== p.x) {
        const endX = kp.x > p.x ? kp.x - nr : kp.x + nr;
        edgeG.appendChild(_svg("line", {
          x1: p.x, y1: kp.y, x2: endX, y2: kp.y,
          stroke: color,
          "stroke-width": 1.6,
          "stroke-linecap": "round",
          "pointer-events": "none",
          class: "history-edge",
        }));
      }
    }
  });

  // Fork sibling dashed lines
  for (const id of forkNodes) {
    const node = tree.byId[id];
    if (!node) continue;
    const pid = node.parent_id;
    if (!pid) continue;
    let sibling: GNode | null = null;
    Object.keys(tree.byId).forEach((sid) => {
      if (sid === id) return;
      const sn = tree.byId[sid];
      if (sn.parent_id === pid && (sn._lane || 0) !== (node._lane || 0)) {
        if (!sibling) sibling = sn;
      }
    });
    if (!sibling) continue;
    const sp = pos(sibling);
    const c = pos(node);
    const nr = NODE_R + 4;
    const startX = sp.x + nr;
    const endX = c.x - nr;
    const color = _branchColor(node, stableLeafOfNode);
    edgeG.appendChild(_svg("line", {
      x1: startX, y1: sp.y, x2: endX, y2: c.y,
      stroke: color,
      "stroke-width": 1.4,
      "stroke-dasharray": "4 3",
      opacity: 0.6,
      "pointer-events": "none",
      class: "history-edge",
    }));
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

  (function _drawBranchTags() {
    const sid = HGW.currentSessionId;
    const rows = (sid && HGW._branchesByConv && HGW._branchesByConv[sid]) || [];
    const named = rows.filter((r) => !!(r.name && (r.name as string).trim()));
    if (!named.length) return;
    const tagG = _svg("g", { class: "history-branch-tags" });
    named.forEach((b) => {
      const node = b.head_msg_id ? tree.byId[b.head_msg_id] : null;
      if (!node) return;
      const p = pos(node);
      const label = b.name as string;
      const dy = 22;
      const color = _branchColor(node, stableLeafOfNode);
      const tg = _svg("g", {
        class: "history-branch-tag",
        transform: "translate(" + p.x + "," + p.y + ")",
      });
      const text = _svg("text", {
        x: "0",
        y: String(dy),
        "text-anchor": "middle",
        "font-size": "10",
        "font-family": "var(--font-sans, sans-serif)",
        "font-weight": "600",
        fill: color,
      });
      text.textContent = label;
      tg.appendChild(text);
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
