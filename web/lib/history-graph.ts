/**
 * Conversation History — PyCharm-style DAG view.
 *
 * TS port of `public/js/shared/history-graph.js`. Self-contained SVG
 * renderer for the right-rail History panel. Exposes
 * `renderHistoryGraph` / `repaintBranchTags` / `setHistoryContextRange`
 * / `refreshHistoryContextRange` / `recomputeHistoryVisibility` on
 * `window.*` (consumed by `conversations.ts`). Imported for side
 * effects by AppShell.
 *
 * Layout: each DAG leaf owns a lane (column); colour encodes branch,
 * shape encodes role (circle=user, triangle=assistant, square=runtime/
 * tool). HEAD is ringed. Click a node → checkout that branch's tip.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */

interface GNode {
  id: string;
  parent_id?: string | null;
  role?: string;
  display?: string;
  created_at?: number;
  function?: string;
  name?: string;
  preview?: string;
  is_named?: boolean;
  head_msg_id?: string;
  children?: GNode[];
  _depth?: number;
  _lane?: number;
  _anchor?: GNode;
  _internal?: boolean;
  _runNode?: boolean;
  [k: string]: any;
}

interface HGWindow {
  currentSessionId?: string | null;
  _branchesByConv?: Record<string, GNode[]>;
  _branchLaneColorMap?: Record<string, string>;
  _postCheckoutScrollTo?: string | null;
  ws?: WebSocket | null;
  [k: string]: any;
}

const HGW = window as unknown as HGWindow;

const ROW_H = 28;
const COL_W = 22;
const NODE_R = 5;
const PAD_X = 18;
const PAD_Y = 16;

const LANE_COLORS = [
  "#4f8ef7",
  "#5aad4e",
  "#d4843a",
  "#9d6fe0",
  "#e0445a",
  "#2db3d5",
  "#d96d2d",
  "#35b89a",
  "#6b8dd6",
  "#2ec4b6",
];

let _currentHead: string | null = null;
let _contextSet: Record<string, boolean> | null = null;
let _visibleIds: Record<string, boolean> = Object.create(null);
let _headAncestorSet: Record<string, boolean> = Object.create(null);
let _internalSet: Record<string, boolean> = Object.create(null);
let _tooltip: HTMLDivElement | null = null;
let _lastSignature: string | null = null;
let _leafOfNode: Record<string, string> = Object.create(null);
let _collapsed: Record<string, boolean> = Object.create(null);
let _seenCollapsible: Record<string, boolean> = Object.create(null);
let _collapseSession: string | null = null;

function _laneColor(i: number): string {
  return LANE_COLORS[i % LANE_COLORS.length];
}

function _signature(graph: GNode[], headId: string | null): string {
  if (!graph || !graph.length) return "empty|" + (headId || "");
  const parts = graph.map(
    (m) =>
      m.id + ":" + (m.parent_id || "") + ":" + (m.role || "") + ":" + (m.display || ""),
  );
  parts.sort();
  return parts.join(",") + "|" + (headId || "");
}

function _collapseRuntimePairs(
  graph: GNode[],
  headId: string | null,
): { graph: GNode[]; headId: string | null } {
  if (!graph || !graph.length) return { graph, headId };
  const childrenOf: Record<string, GNode[]> = Object.create(null);
  graph.forEach((m) => {
    if (m.parent_id) (childrenOf[m.parent_id] = childrenOf[m.parent_id] || []).push(m);
  });
  const removeIds: Record<string, boolean> = Object.create(null);
  const reparent: Record<string, string | null> = Object.create(null);
  const userToAsst: Record<string, string> = Object.create(null);
  graph.forEach((m) => {
    if (m.role !== "user" || m.display !== "runtime") return;
    const kids = childrenOf[m.id] || [];
    if (kids.length !== 1) return;
    const c = kids[0];
    if (c.role !== "assistant" || c.display !== "runtime") return;
    removeIds[m.id] = true;
    reparent[c.id] = m.parent_id || null;
    userToAsst[m.id] = c.id;
  });
  if (headId && userToAsst[headId]) headId = userToAsst[headId];
  const collapsed: GNode[] = [];
  graph.forEach((m) => {
    if (removeIds[m.id]) return;
    if (m.id in reparent) {
      collapsed.push(Object.assign({}, m, { parent_id: reparent[m.id] }));
    } else {
      collapsed.push(m);
    }
  });
  return { graph: collapsed, headId };
}

function _mergeRuns(
  graph: GNode[],
  headId: string | null,
): { graph: GNode[]; headId: string | null } {
  if (!graph || !graph.length) return { graph, headId };
  const idx: Record<string, number> = Object.create(null);
  graph.forEach((m, i) => {
    idx[m.id] = i;
  });
  const kidsOf: Record<string, GNode[]> = Object.create(null);
  graph.forEach((m) => {
    if (m.parent_id) (kidsOf[m.parent_id] = kidsOf[m.parent_id] || []).push(m);
  });
  const removeIds: Record<string, boolean> = Object.create(null);
  const runNode: Record<string, boolean> = Object.create(null);
  const mergeTarget: Record<string, string> = Object.create(null);
  const internalOf: Record<string, string> = Object.create(null);
  Object.keys(kidsOf).forEach((pid) => {
    const kids = kidsOf[pid];
    const tools = kids.filter((k) => k.role === "tool");
    if (!tools.length) return;
    tools.sort((a, b) => idx[a.id] - idx[b.id]);
    kids.forEach((k) => {
      if (k.role === "tool") return;
      let t: GNode | null = null;
      for (let i = 0; i < tools.length; i++) {
        if (idx[tools[i].id] < idx[k.id]) t = tools[i];
        else break;
      }
      if (!t || removeIds[t.id]) return;
      removeIds[t.id] = true;
      mergeTarget[t.id] = k.id;
      runNode[k.id] = true;
      const stack = (kidsOf[t.id] || []).slice();
      while (stack.length) {
        const ic = stack.pop()!;
        if (ic.id in internalOf) continue;
        internalOf[ic.id] = k.id;
        (kidsOf[ic.id] || []).forEach((g) => stack.push(g));
      }
    });
  });
  if (!Object.keys(removeIds).length) return { graph, headId };
  if (headId && mergeTarget[headId]) headId = mergeTarget[headId];

  const reparent: Record<string, string> = Object.create(null);
  graph.forEach((m) => {
    const pid = m.parent_id;
    if (!pid) return;
    if (m.id in internalOf) {
      if (removeIds[pid]) reparent[m.id] = internalOf[m.id];
    } else if (pid in internalOf) {
      reparent[m.id] = internalOf[pid];
    }
  });

  const byId: Record<string, GNode> = Object.create(null);
  graph.forEach((m) => {
    byId[m.id] = m;
  });
  function _build(m: GNode): GNode {
    let nm: GNode | null = null;
    if (m.id in reparent) {
      nm = Object.assign({}, m);
      nm.parent_id = reparent[m.id];
    }
    if (m.id in internalOf) {
      nm = nm || Object.assign({}, m);
      nm._internal = true;
    }
    if (runNode[m.id]) {
      nm = nm || Object.assign({}, m);
      nm._runNode = true;
    }
    return nm || m;
  }
  const emitted: Record<string, boolean> = Object.create(null);
  const out: GNode[] = [];
  graph.forEach((m) => {
    if (removeIds[m.id]) {
      const tgt = mergeTarget[m.id];
      if (tgt && !emitted[tgt] && byId[tgt]) {
        emitted[tgt] = true;
        out.push(_build(byId[tgt]));
      }
      return;
    }
    if (emitted[m.id]) return;
    emitted[m.id] = true;
    out.push(_build(m));
  });
  return { graph: out, headId };
}

function _buildTree(graph: GNode[]): { roots: GNode[]; byId: Record<string, GNode> } {
  const byId: Record<string, GNode> = Object.create(null);
  graph.forEach((m) => {
    byId[m.id] = Object.assign({ children: [] }, m);
  });
  const roots: GNode[] = [];
  graph.forEach((m) => {
    const node = byId[m.id];
    if (m.parent_id && byId[m.parent_id]) byId[m.parent_id].children!.push(node);
    else roots.push(node);
  });
  function byTs(a: GNode, b: GNode): number {
    return (a.created_at || 0) - (b.created_at || 0);
  }
  roots.sort(byTs);
  Object.keys(byId).forEach((id) => byId[id].children!.sort(byTs));
  return { roots, byId };
}

function _applyCollapse(graph: GNode[]): {
  visible: GNode[];
  hiddenCount: Record<string, number>;
  isCollapsible: (m: GNode) => boolean;
} {
  const sid = HGW.currentSessionId || null;
  if (sid !== _collapseSession) {
    _collapsed = Object.create(null);
    _seenCollapsible = Object.create(null);
    _collapseSession = sid;
  }
  const childrenOf: Record<string, string[]> = Object.create(null);
  const internalFlag: Record<string, boolean> = Object.create(null);
  graph.forEach((m) => {
    if (m._internal) internalFlag[m.id] = true;
    if (m.parent_id) {
      (childrenOf[m.parent_id] = childrenOf[m.parent_id] || []).push(m.id);
    }
  });
  function _internalKids(id: string): string[] {
    return (childrenOf[id] || []).filter((c) => internalFlag[c]);
  }
  function collapsible(m: GNode): boolean {
    if (m.role === "tool") return (childrenOf[m.id] || []).length > 0;
    if (m._runNode) return _internalKids(m.id).length > 0;
    return false;
  }
  graph.forEach((m) => {
    if (collapsible(m) && !_seenCollapsible[m.id]) {
      _seenCollapsible[m.id] = true;
      _collapsed[m.id] = true;
    }
  });
  const hidden: Record<string, boolean> = Object.create(null);
  const hiddenCount: Record<string, number> = Object.create(null);
  graph.forEach((m) => {
    if (!_collapsed[m.id]) return;
    const stack = m._runNode
      ? _internalKids(m.id)
      : (childrenOf[m.id] || []).slice();
    let cnt = 0;
    while (stack.length) {
      const id = stack.pop()!;
      if (hidden[id]) continue;
      hidden[id] = true;
      cnt++;
      const kids = childrenOf[id] || [];
      for (let i = 0; i < kids.length; i++) {
        if (m._runNode && !internalFlag[kids[i]]) continue;
        stack.push(kids[i]);
      }
    }
    hiddenCount[m.id] = cnt;
  });
  return {
    visible: graph.filter((m) => !hidden[m.id]),
    hiddenCount,
    isCollapsible: collapsible,
  };
}

function _assignDepth(ordered: GNode[], byId: Record<string, GNode>): number {
  let row = 0;
  ordered.forEach((m) => {
    const n = byId[m.id];
    if (n && n._depth === undefined) n._depth = row++;
  });
  Object.keys(byId).forEach((id) => {
    if (byId[id]._depth === undefined) byId[id]._depth = row++;
  });
  return Math.max(row - 1, 0);
}

function _headAncestors(byId: Record<string, GNode>, headId: string | null): string[] {
  const out: string[] = [];
  let cur = headId;
  while (cur && byId[cur]) {
    out.push(cur);
    cur = byId[cur].parent_id || null;
  }
  return out;
}

function _branchAnchor(leaf: GNode, byId: Record<string, GNode>): GNode {
  let cur: GNode | null = leaf;
  while (cur) {
    const parent: GNode | null = cur.parent_id ? byId[cur.parent_id] : null;
    if (!parent) return cur;
    if (parent.children!.length > 1) return cur;
    cur = parent;
  }
  return leaf;
}

function _assignLanes(
  byId: Record<string, GNode>,
): { leaves: GNode[]; laneCount: number; leafOfNode: Record<string, string> } {
  const leaves: GNode[] = [];
  Object.keys(byId).forEach((id) => {
    if (!byId[id].children!.length) leaves.push(byId[id]);
  });

  // Internal execution nodes (an @agentic_function's LLM / tool calls)
  // always sort LAST, regardless of created_at. An internal call
  // usually has an earlier seq than the conversation that continues
  // after the run, so by raw created_at it would claim lane 0 and
  // shove the conversation trunk onto a side lane — the trunk would
  // change colour just by expanding a run. Forcing internal leaves to
  // the back keeps the conversation on lane 0 and makes internal calls
  // branch off to the side: detail hanging off the trunk, not a peer.
  leaves.forEach((leaf) => {
    leaf._anchor = _branchAnchor(leaf, byId);
  });
  leaves.sort((a, b) => {
    const ax = a._internal ? 1 : 0;
    const bx = b._internal ? 1 : 0;
    if (ax !== bx) return ax - bx;
    const dt = (a._anchor!.created_at || 0) - (b._anchor!.created_at || 0);
    if (dt !== 0) return dt;
    const ai = a._anchor!.id;
    const bi = b._anchor!.id;
    return ai < bi ? -1 : ai > bi ? 1 : 0;
  });

  const leafOfNode: Record<string, string> = Object.create(null);
  leaves.forEach((leaf, laneIdx) => {
    leaf._lane = laneIdx;
    let cur: GNode | null = leaf;
    while (cur) {
      if (cur._lane === undefined) cur._lane = laneIdx;
      leafOfNode[cur.id] = leaf.id;
      const parent: GNode | null = cur.parent_id ? byId[cur.parent_id] : null;
      if (parent && parent._lane !== undefined && parent._lane !== laneIdx) break;
      cur = parent;
    }
  });

  Object.keys(byId).forEach((id) => {
    if (byId[id]._lane === undefined) byId[id]._lane = 0;
    if (!(id in leafOfNode)) leafOfNode[id] = id;
  });

  return { leaves, laneCount: leaves.length || 1, leafOfNode };
}

function _svg(tag: string, attrs?: Record<string, string | number>): SVGElement {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  if (attrs) Object.keys(attrs).forEach((k) => el.setAttribute(k, String(attrs[k])));
  return el as SVGElement;
}

function _edgePath(x1: number, y1: number, x2: number, y2: number): string {
  if (x1 === x2) return "M" + x1 + "," + y1 + " L" + x2 + "," + y2;
  const my = (y1 + y2) / 2;
  return (
    "M" + x1 + "," + y1 + " C" + x1 + "," + my + " " + x2 + "," + my + " " + x2 + "," + y2
  );
}

function _shapeFor(node: GNode): string {
  const role = node.role;
  const display = node.display;
  if (display === "runtime") return "square";
  if (role === "tool") return "square";
  if (role === "assistant") return "triangle";
  if (role === "user") return "circle";
  return "circle";
}

function _labelFor(node: GNode): string {
  if (node.role === "tool") return node.function || node.name || "function";
  if (node.display === "runtime") return node.function || "runtime";
  if (node.preview) return node.preview;
  if (node.role === "user") return "You";
  if (node.role === "assistant") return "Agent";
  return node.role || "?";
}

function _fitLabel(text: string, maxW: number): string {
  const max = Math.max(4, Math.floor(maxW / 6.2));
  if (text.length <= max) return text;
  return text.slice(0, max - 1) + "…";
}

function _applyShapeSize(shape: SVGElement, isCurrent: boolean): void {
  const r = isCurrent ? NODE_R + 1.8 : NODE_R;
  if (shape.tagName === "circle") {
    shape.setAttribute("r", String(r));
  } else if (shape.tagName === "polygon") {
    const t = r * 1.5;
    const COS30 = 0.8660254;
    shape.setAttribute(
      "points",
      "0," + -t + " " + t * COS30 + "," + t * 0.5 + " " + -t * COS30 + "," + t * 0.5,
    );
  } else if (shape.tagName === "rect") {
    const s = r - 0.2;
    shape.setAttribute("x", String(-s));
    shape.setAttribute("y", String(-s));
    shape.setAttribute("width", String(s * 2));
    shape.setAttribute("height", String(s * 2));
  }
}

function _buildShapeEl(shape: string, color: string, r: number): SVGElement | null {
  if (shape === "circle") {
    return _svg("circle", { r, fill: color });
  } else if (shape === "triangle") {
    const t = r * 1.5;
    const COS30 = 0.8660254;
    return _svg("polygon", {
      points:
        "0," + -t + " " + t * COS30 + "," + t * 0.5 + " " + -t * COS30 + "," + t * 0.5,
      fill: color,
    });
  } else if (shape === "square") {
    const s = r - 0.2;
    return _svg("rect", {
      x: -s,
      y: -s,
      width: s * 2,
      height: s * 2,
      rx: 0.8,
      ry: 0.8,
      fill: color,
    });
  }
  return null;
}

const CURSOR_R = NODE_R * 0.55;

function _shapeTypeFromTag(tagName: string): string {
  if (tagName === "polygon") return "triangle";
  if (tagName === "rect") return "square";
  return "circle";
}

function _ensureTooltip(body: HTMLElement): HTMLDivElement {
  if (_tooltip && _tooltip.parentElement === body) return _tooltip;
  _tooltip = document.createElement("div");
  _tooltip.className = "history-tooltip";
  body.appendChild(_tooltip);
  return _tooltip;
}

function _showTooltip(body: HTMLElement, node: GNode, x: number, y: number): void {
  const tip = _ensureTooltip(body);
  const role =
    node.display === "runtime"
      ? "runtime · " + (node.function || "")
      : node.role || "?";
  tip.innerHTML = "";
  const r = document.createElement("div");
  r.className = "history-tooltip-role";
  r.textContent = role;
  tip.appendChild(r);
  const p = document.createElement("div");
  p.textContent = node.preview || "(empty)";
  tip.appendChild(p);
  const bw = body.clientWidth;
  tip.classList.add("visible");
  const tw = tip.offsetWidth;
  let left = x + 14;
  if (left + tw > bw - 6) left = Math.max(6, x - 14 - tw);
  tip.style.left = left + "px";
  tip.style.top = Math.max(6, y - 10) + "px";
}

function _hideTooltip(): void {
  if (_tooltip) _tooltip.classList.remove("visible");
}

function render(graphIn: GNode[], headIdIn: string | null): void {
  let graph = graphIn;
  let headId = headIdIn;

  const merged = _mergeRuns(graph, headId);
  graph = merged.graph;
  headId = merged.headId;

  const collapsedR = _collapseRuntimePairs(graph, headId);
  graph = collapsedR.graph;
  headId = collapsedR.headId;

  const cinfo = _applyCollapse(graph);
  graph = cinfo.visible;

  const sig = _signature(graph, headId);
  if (sig === _lastSignature && _currentHead === headId) return;
  _lastSignature = sig;
  _currentHead = headId;

  const panel = document.getElementById("historyPanel");
  if (!panel) return;
  const body = panel.querySelector(".history-body") as HTMLElement | null;
  if (!body) return;

  if (!graph || !graph.length) {
    const empty = document.createElement("div");
    empty.className = "history-empty";
    empty.textContent = "No messages yet.";
    body.replaceChildren(empty);
    _tooltip = null;
    _leafOfNode = Object.create(null);
    return;
  }

  const tree = _buildTree(graph);
  const maxDepth = _assignDepth(graph, tree.byId);
  const lanes = _assignLanes(tree.byId);
  _leafOfNode = lanes.leafOfNode;

  const _colorMap: Record<string, string> = Object.create(null);
  Object.keys(tree.byId).forEach((id) => {
    const node = tree.byId[id];
    if (node._lane !== undefined) _colorMap[id] = _laneColor(node._lane);
  });
  HGW._branchLaneColorMap = _colorMap;

  const headAncestors: Record<string, boolean> = Object.create(null);
  _headAncestors(tree.byId, headId).forEach((id) => {
    headAncestors[id] = true;
  });
  _headAncestorSet = headAncestors;

  const internalSet: Record<string, boolean> = Object.create(null);
  const internalOwner: Record<string, string> = Object.create(null);
  Object.keys(tree.byId).forEach((rootId) => {
    const rootNode = tree.byId[rootId];
    const isRunNode = !!rootNode._runNode;
    if (rootNode.role !== "tool" && !isRunNode) return;
    const owner = isRunNode ? rootId : rootNode.parent_id || null;
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
  _internalSet = internalSet;

  const laneArea = PAD_X + COL_W * Math.max(lanes.laneCount - 1, 0);
  const labelX = laneArea + 16;
  const panelW = (body && body.clientWidth) || 240;
  const width = Math.max(panelW - 4, labelX + 90);
  const labelMaxW = width - labelX - 10;
  const height = PAD_Y * 2 + ROW_H * maxDepth;

  const svg = _svg("svg", {
    class: "history-svg",
    viewBox: "0 0 " + Math.max(width, 40) + " " + Math.max(height, 40),
    width: Math.max(width, 40),
    height: Math.max(height, 40),
  });

  const edgeG = _svg("g", { class: "history-edges" });
  const nodeG = _svg("g", { class: "history-nodes" });
  svg.appendChild(edgeG);
  svg.appendChild(nodeG);

  function pos(n: GNode): { x: number; y: number } {
    return { x: PAD_X + (n._lane || 0) * COL_W, y: PAD_Y + (n._depth || 0) * ROW_H };
  }

  Object.keys(tree.byId).forEach((id) => {
    const node = tree.byId[id];
    if (!node.parent_id || !tree.byId[node.parent_id]) return;
    const parent = tree.byId[node.parent_id];
    const p = pos(parent);
    const c = pos(node);
    const color = _laneColor(node._lane || 0);
    const onHead = headAncestors[id] && headAncestors[node.parent_id];
    edgeG.appendChild(
      _svg("path", {
        d: _edgePath(p.x, p.y, c.x, c.y),
        stroke: color,
        "stroke-width": onHead ? 2 : 1.6,
        fill: "none",
        "stroke-linecap": "round",
        opacity: onHead ? 1 : 0.85,
        class:
          "history-edge" +
          (onHead ? " on-head" : "") +
          (_contextSet && !_contextSet[id] ? " out-of-context" : ""),
      }),
    );
  });

  Object.keys(tree.byId).forEach((id) => {
    const node = tree.byId[id];
    const p = pos(node);
    const isHead = id === headId;
    const onHead = !!headAncestors[id];
    const color = _laneColor(node._lane || 0);
    const isCollapsible = cinfo.isCollapsible(node);
    const isFolded = isCollapsible && !!_collapsed[id];
    const g = _svg("g", {
      class:
        "history-node" +
        (isHead ? " is-head" : "") +
        (onHead ? "" : " off-head") +
        (isCollapsible ? " is-collapsible" : "") +
        (_contextSet && !_contextSet[id] ? " out-of-context" : ""),
      transform: "translate(" + p.x + "," + p.y + ")",
      "data-msg-id": id,
      "data-collapsible": isCollapsible ? "1" : "0",
      "data-collapsed": isFolded ? "1" : "0",
      "data-internal": internalSet[id] ? "1" : "0",
      "data-owner": internalOwner[id] || "",
    });
    const hit = _svg("circle", {
      r: "14",
      fill: "transparent",
      "pointer-events": "all",
    });
    g.appendChild(hit);
    (g as SVGGraphicsElement).style.cursor = "pointer";
    const r = onHead ? NODE_R : NODE_R * 0.7;
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
      badge.textContent = isFolded ? "+" + hc : "−";
      g.appendChild(badge);
    }
    (g as any)._nodeData = node;
    nodeG.appendChild(g);
  });

  const labelG = _svg("g", { class: "history-labels" });
  Object.keys(tree.byId).forEach((id) => {
    const node = tree.byId[id];
    const p = pos(node);
    const onHead = !!headAncestors[id];
    const text = _svg("text", {
      x: String(labelX),
      y: String(p.y),
      class:
        "history-label" + (onHead ? " on-head" : "") + (id === headId ? " is-head" : ""),
      "data-msg-id": id,
    });
    text.textContent = _fitLabel(_labelFor(node), labelMaxW);
    labelG.appendChild(text);
  });
  svg.appendChild(labelG);

  (function _drawBranchTags() {
    const sid = HGW.currentSessionId;
    const rows = (sid && HGW._branchesByConv && HGW._branchesByConv[sid]) || [];
    const named = rows.filter((r) => r.is_named && r.name);
    if (!named.length) return;
    const tagG = _svg("g", { class: "history-branch-tags" });
    named.forEach((b) => {
      const node = b.head_msg_id ? tree.byId[b.head_msg_id] : null;
      if (!node) return;
      const p = pos(node);
      const label = b.name as string;
      const textW = Math.ceil(label.length * 7.2);
      const pad = 6;
      const w = textW + pad * 2;
      const h = 16;
      const dy = -22;
      const tg = _svg("g", {
        class: "history-branch-tag",
        transform: "translate(" + p.x + "," + p.y + ")",
      });
      const rect = _svg("rect", {
        x: String(-w / 2),
        y: String(dy - h / 2),
        width: String(w),
        height: String(h),
        rx: "3",
        fill: "#3aafa9",
      });
      const text = _svg("text", {
        x: "0",
        y: String(dy + 4),
        "text-anchor": "middle",
        "font-size": "11",
        "font-family": "var(--font-sans, sans-serif)",
        fill: "#fff",
      });
      text.textContent = label;
      tg.appendChild(rect);
      tg.appendChild(text);
      tagG.appendChild(tg);
    });
    svg.appendChild(tagG);
  })();

  body.replaceChildren(svg);
  _tooltip = null;
  _visibleIds = Object.create(null);

  _wireChatScrollSync();
  _wireChatMutationSync();
  _wirePanelResize();
  _recomputeVisibility();
  requestAnimationFrame(_recomputeVisibility);
  setTimeout(_recomputeVisibility, 250);
  setTimeout(_recomputeVisibility, 700);

  const bodyAny = body as any;
  if (!bodyAny._historyHoverWired) {
    bodyAny._historyHoverWired = true;
    body.addEventListener("mousemove", (e: MouseEvent) => {
      const tgt = e.target as HTMLElement;
      const g = tgt.closest && (tgt.closest(".history-node") as any);
      if (!g || !g._nodeData) {
        _hideTooltip();
        return;
      }
      const rect = body.getBoundingClientRect();
      _showTooltip(
        body,
        g._nodeData,
        e.clientX - rect.left + body.scrollLeft,
        e.clientY - rect.top + body.scrollTop,
      );
    });
    body.addEventListener("mouseleave", _hideTooltip);
  }
}

function _applyVisibility(nodeEl: Element, visible: boolean): void {
  let shape: SVGElement | null = null;
  const kids = nodeEl.children;
  for (let i = 0; i < kids.length; i++) {
    const c = kids[i];
    const tag = c.tagName;
    if (tag !== "circle" && tag !== "polygon" && tag !== "rect") continue;
    if (c.getAttribute("fill") === "transparent") continue;
    if (c.classList && c.classList.contains("n-inner")) continue;
    shape = c as SVGElement;
    break;
  }
  if (shape) _applyShapeSize(shape, visible);
  const inner = nodeEl.querySelector(".n-inner");
  if (visible) {
    if (!inner && shape) {
      const shapeType = _shapeTypeFromTag(shape.tagName);
      const built = _buildShapeEl(shapeType, "#ffffff", CURSOR_R);
      if (built) {
        built.setAttribute("class", "n-inner");
        built.setAttribute(
          "style",
          "opacity: 0; transition: opacity 180ms ease; pointer-events: none;",
        );
        nodeEl.appendChild(built);
        const el = built;
        requestAnimationFrame(() => {
          el.setAttribute(
            "style",
            "opacity: 1; transition: opacity 180ms ease; pointer-events: none;",
          );
        });
      }
    }
  } else if (inner) {
    inner.parentNode!.removeChild(inner);
  }
}

function _setVisibleSet(newSet: Record<string, boolean>): void {
  const panel = document.getElementById("historyPanel");
  if (!panel) return;
  const body = panel.querySelector(".history-body") as HTMLElement | null;
  if (!body) return;
  const visibleEls: Element[] = [];
  body.querySelectorAll(".history-node").forEach((g) => {
    const id = g.getAttribute("data-msg-id") || "";
    const nowVisible = !!newSet[id];
    const wasVisible = !!_visibleIds[id];
    if (nowVisible !== wasVisible) _applyVisibility(g, nowVisible);
    if (nowVisible) visibleEls.push(g);
  });
  _visibleIds = newSet;

  if (visibleEls.length && !_userScrolledGraph) {
    const mid = visibleEls[Math.floor(visibleEls.length / 2)];
    const nodeRect = mid.getBoundingClientRect();
    const bodyRect = body.getBoundingClientRect();
    const nodeY = nodeRect.top - bodyRect.top + body.scrollTop;
    const desired = body.clientHeight * 0.45;
    let targetScroll = nodeY - desired;
    const maxScroll = Math.max(0, body.scrollHeight - body.clientHeight);
    if (targetScroll < 0) targetScroll = 0;
    if (targetScroll > maxScroll) targetScroll = maxScroll;
    if (Math.abs(targetScroll - body.scrollTop) > 24) {
      body.scrollTo({ top: targetScroll, behavior: "smooth" });
    }
  }
}

let _userScrolledGraph = false;
let _userScrollTimer = 0;
function _wireGraphManualScroll(): void {
  const body = document.querySelector("#historyPanel .history-body") as
    | (HTMLElement & { _manualScrollWired?: boolean })
    | null;
  if (!body || body._manualScrollWired) return;
  body._manualScrollWired = true;
  body.addEventListener(
    "wheel",
    () => {
      _userScrolledGraph = true;
      clearTimeout(_userScrollTimer);
      _userScrollTimer = window.setTimeout(() => {
        _userScrolledGraph = false;
      }, 1500);
    },
    { passive: true },
  );
}

function _recomputeVisibility(): void {
  const area = document.getElementById("chatArea");
  if (!area) return;
  const container = document.getElementById("chatMessages");
  if (!container) return;
  const rect = area.getBoundingClientRect();
  const bubbles = container.querySelectorAll("[data-msg-id], [data-msg-ids]");
  const newSet: Record<string, boolean> = Object.create(null);
  for (let i = 0; i < bubbles.length; i++) {
    const br = bubbles[i].getBoundingClientRect();
    if (br.bottom <= rect.top || br.top >= rect.bottom) continue;
    const multi = bubbles[i].getAttribute("data-msg-ids");
    if (multi) {
      const parts = multi.split(/\s+/);
      for (let j = 0; j < parts.length; j++) {
        if (parts[j]) newSet[parts[j]] = true;
      }
    } else {
      const single = bubbles[i].getAttribute("data-msg-id");
      if (single) newSet[single] = true;
    }
  }
  _setVisibleSet(newSet);
}

function _chatBubbleFor(msgId: string): Element | null {
  if (!msgId) return null;
  const container = document.getElementById("chatMessages");
  if (!container) return null;
  const esc = window.CSS && CSS.escape ? CSS.escape(msgId) : msgId;
  return (
    container.querySelector('[data-msg-id="' + esc + '"]') ||
    container.querySelector('[data-msg-ids~="' + esc + '"]')
  );
}

function _scrollChatTo(msgId: string): void {
  const bubble = _chatBubbleFor(msgId);
  if (!bubble) return;
  bubble.scrollIntoView({ behavior: "smooth", block: "start" });
}

let _chatScrollWired = false;
function _wireChatScrollSync(): void {
  if (_chatScrollWired) return;
  const area = document.getElementById("chatArea");
  if (!area) return;
  _chatScrollWired = true;
  let raf = 0;
  area.addEventListener(
    "scroll",
    () => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        _recomputeVisibility();
        _wireGraphManualScroll();
      });
    },
    { passive: true },
  );
}

let _chatMutationWired = false;
function _wireChatMutationSync(): void {
  if (_chatMutationWired) return;
  if (typeof MutationObserver === "undefined") return;
  const container = document.getElementById("chatMessages");
  if (!container) return;
  _chatMutationWired = true;
  let raf = 0;
  const mo = new MutationObserver(() => {
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      _recomputeVisibility();
    });
  });
  mo.observe(container, { childList: true, subtree: true });
}

async function _checkout(msgId: string): Promise<void> {
  const sessionId = HGW.currentSessionId;
  if (!sessionId || !msgId) return;
  const target = _leafOfNode[msgId] || msgId;
  if (target === _currentHead) return;
  try {
    const r = await fetch("/api/chat/checkout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, msg_id: target }),
    });
    if (!r.ok) throw new Error(await r.text());
    HGW._postCheckoutScrollTo = msgId;
    if (HGW.ws && HGW.ws.readyState === WebSocket.OPEN) {
      HGW.ws.send(JSON.stringify({ action: "load_session", session_id: sessionId }));
    }
  } catch (err) {
    console.error("[history-graph] checkout failed:", err);
  }
}

document.addEventListener("click", (e) => {
  const tgt = e.target as HTMLElement;
  const g = tgt.closest && tgt.closest(".history-node");
  if (!g) return;
  const id = g.getAttribute("data-msg-id");
  if (!id) return;
  if (g.getAttribute("data-collapsible") === "1") {
    if (_collapsed[id]) delete _collapsed[id];
    else _collapsed[id] = true;
    if (_lastGraph) {
      _lastSignature = null;
      render(_lastGraph, _lastHeadId);
    }
    return;
  }
  if (g.getAttribute("data-internal") === "1") {
    const owner = g.getAttribute("data-owner");
    if (owner) _scrollChatTo(owner);
    return;
  }
  if (_headAncestorSet[id]) {
    _scrollChatTo(id);
  } else {
    _checkout(id);
  }
});

let _lastGraph: GNode[] | null = null;
let _lastHeadId: string | null = null;

export function renderHistoryGraph(graph: GNode[], headId: string | null): void {
  _lastGraph = graph;
  _lastHeadId = headId;
  render(graph, headId);
}

export function repaintBranchTags(): void {
  if (_lastGraph) render(_lastGraph, _lastHeadId);
}

export function setHistoryContextRange(ids: string[] | null): void {
  if (!ids || !ids.length) {
    _contextSet = null;
  } else {
    _contextSet = Object.create(null);
    for (let i = 0; i < ids.length; i++) _contextSet![ids[i]] = true;
  }
  if (_lastGraph) {
    _lastSignature = null;
    render(_lastGraph, _lastHeadId);
  }
}

export function refreshHistoryContextRange(sessionId: string | null): void {
  if (!sessionId) {
    setHistoryContextRange(null);
    return;
  }
  fetch("/api/sessions/" + encodeURIComponent(sessionId) + "/context-range")
    .then((r) => (r.ok ? r.json() : null))
    .then((j) => {
      if (j) setHistoryContextRange(j.node_ids || []);
    })
    .catch(() => {
      /* leave undimmed on failure */
    });
}

export function recomputeHistoryVisibility(): void {
  _recomputeVisibility();
}

let _panelResizeWired = false;
function _wirePanelResize(): void {
  if (_panelResizeWired) return;
  if (typeof ResizeObserver === "undefined") return;
  const panel = document.getElementById("historyPanel");
  if (!panel) return;
  const body = panel.querySelector(".history-body") as HTMLElement | null;
  if (!body) return;
  _panelResizeWired = true;
  let lastW = body.clientWidth;
  let raf = 0;
  const ro = new ResizeObserver(() => {
    const w = body.clientWidth;
    if (w === lastW || !_lastGraph) return;
    lastW = w;
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      _lastSignature = null;
      render(_lastGraph!, _lastHeadId);
    });
  });
  ro.observe(body);
}

/* ===== window bridges ============================================ */

HGW.renderHistoryGraph = renderHistoryGraph;
HGW.repaintBranchTags = repaintBranchTags;
HGW.setHistoryContextRange = setHistoryContextRange;
HGW.refreshHistoryContextRange = refreshHistoryContextRange;
HGW.recomputeHistoryVisibility = recomputeHistoryVisibility;

void _internalSet;
