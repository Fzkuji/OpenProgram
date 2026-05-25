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
  _tier?: number;
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

const ROW_H = 32;
const COL_W = 48;
const NODE_R = 5;
const PAD_X = 18;
const PAD_Y = 16;

// Index 0 is the trunk colour; 1..N-1 are side-branch colours, picked
// by a hash of the branch's leaf id. Distinct, evenly-spread hues so
// neighbouring branches never read as the same colour.
const LANE_COLORS = [
  "#4f8ef7", // blue        (trunk)
  "#5aad4e", // green
  "#d4843a", // orange
  "#9d6fe0", // purple
  "#e0445a", // red
  "#2db3d5", // cyan
  "#e0b020", // gold
  "#35b89a", // teal
  "#e066b3", // magenta
  "#6b8dd6", // slate blue
  "#8fbf3f", // lime
  "#d9694f", // coral
  "#52c4c4", // aqua
  "#b08be0", // lavender
  "#c79a4a", // tan
  "#e08a3a", // amber
  "#6fae6f", // sage
  "#d05fa0", // rose
];

let _currentHead: string | null = null;
let _contextSet: Record<string, boolean> | null = null;
// "viewport": white-fill follows chat-scroll position (which message
// the user is currently looking at).
// "context":  white-fill marks the message set the next LLM call
// will load as context (read from /api/sessions/:id/context-range).
// Toolbar in <HistoryGraphPanel /> flips between the two.
type HighlightMode = "viewport" | "context";
let _highlightMode: HighlightMode = "viewport";
let _visibleIds: Record<string, boolean> = Object.create(null);
let _headAncestorSet: Record<string, boolean> = Object.create(null);
let _internalSet: Record<string, boolean> = Object.create(null);
// internal node id → the id of the turn that owns it (the runtime
// block / tool call it surfaces inside). Used so an internal node
// lights up whenever its owner turn is on screen — internal nodes
// have no chat bubble of their own to drive `_recomputeVisibility`.
let _internalOwner: Record<string, string> = Object.create(null);
let _tooltip: HTMLDivElement | null = null;
let _lastSignature: string | null = null;
let _leafOfNode: Record<string, string> = Object.create(null);
let _collapsed: Record<string, boolean> = Object.create(null);
// `_seenCollapsible` is assigned across renders to remember whether a node was
// ever rendered as a collapsible cluster within the current session. Even
// though no read site references it today, it is intentionally retained so
// future debug tooling (or a session-scoped diff) can pick it up without
// reinstating the bookkeeping. ESLint sees it as unused — squelch it locally.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
let _seenCollapsible: Record<string, boolean> = Object.create(null);
let _collapseSession: string | null = null;

/** Stable per-branch colour. Lane 0 (the trunk) is always
 *  LANE_COLORS[0]. A side branch is coloured by a hash of its leaf id
 *  — a stable node id — NOT by its lane index. So checking out a
 *  different branch (which reshuffles lane indices) does not repaint
 *  the other branches: each keeps the colour tied to its identity. */
function _branchColor(node: GNode, leafOfNode: Record<string, string>): string {
  if ((node._lane || 0) === 0) return LANE_COLORS[0];
  const leafId = leafOfNode[node.id] || node.id;
  let h = 0;
  for (let i = 0; i < leafId.length; i++) {
    h = (h * 31 + leafId.charCodeAt(i)) | 0;
  }
  // Reserve index 0 for the trunk; side branches use 1..N-1.
  return LANE_COLORS[1 + (Math.abs(h) % (LANE_COLORS.length - 1))];
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
  const callerKidsOf: Record<string, string[]> = Object.create(null);
  const internalFlag: Record<string, boolean> = Object.create(null);
  graph.forEach((m) => {
    if (m._internal) internalFlag[m.id] = true;
    if (m.parent_id) {
      (childrenOf[m.parent_id] = childrenOf[m.parent_id] || []).push(m.id);
    }
    // Sub-call edge: any node with this one as `caller` is a child
    // of this one's call-stack frame. Collapsible if any such kid
    // exists — that lets us hide the tool sub-tree under its owning
    // assistant.
    const ca = m.caller;
    if (ca) {
      (callerKidsOf[ca] = callerKidsOf[ca] || []).push(m.id);
    }
  });
  function _internalKids(id: string): string[] {
    return (childrenOf[id] || []).filter((c) => internalFlag[c]);
  }
  // Collapse only sub-call subtrees (tool calls + tool-spawned LLM
  // calls hanging off an assistant via the `caller` edge). Main
  // conv-chain nodes (user / assistant on parent_id-edges) never
  // collapse — they ARE the DAG. So a node is collapsible iff it
  // owns at least one caller-edge child; that's the tool cluster
  // hanging off it.
  function collapsible(m: GNode): boolean {
    if ((callerKidsOf[m.id] || []).length > 0) return true;
    if (m.role === "tool") return (childrenOf[m.id] || []).length > 0;
    if (m._runNode) return _internalKids(m.id).length > 0;
    return false;
  }
  // Auto-collapse big tool clusters so 30 read() calls don't fill the
  // panel. AUTO_COLLAPSE_THRESHOLD is the kid count above which the
  // cluster folds by default; user can click to expand.
  const AUTO_COLLAPSE_THRESHOLD = 4;
  graph.forEach((m) => {
    if (!collapsible(m)) return;
    if (_seenCollapsible[m.id]) return;
    _seenCollapsible[m.id] = true;
    const kidCount = (callerKidsOf[m.id] || []).length;
    if (kidCount > AUTO_COLLAPSE_THRESHOLD) {
      _collapsed[m.id] = true;
    }
  });
  const hidden: Record<string, boolean> = Object.create(null);
  const hiddenCount: Record<string, number> = Object.create(null);
  graph.forEach((m) => {
    if (!_collapsed[m.id]) return;
    // Walk only the sub-call subtree (caller-edge descendants), not
    // the conversation children. Tools / FunctionCall sub-calls hide
    // when their owning assistant collapses; the next user turn (a
    // conv child) stays visible.
    const hasCallerKids = (callerKidsOf[m.id] || []).length > 0;
    const stack = hasCallerKids
      ? (callerKidsOf[m.id] || []).slice()
      : m._runNode
        ? _internalKids(m.id)
        : (childrenOf[m.id] || []).slice();
    let cnt = 0;
    while (stack.length) {
      const id = stack.pop()!;
      if (hidden[id]) continue;
      hidden[id] = true;
      cnt++;
      // Recurse into the same kind of children (caller-edge or
      // legacy internal/parent edge depending on which expansion
      // started this collapse).
      const kids = hasCallerKids
        ? (callerKidsOf[id] || [])
        : (childrenOf[id] || []);
      for (let i = 0; i < kids.length; i++) {
        if (!hasCallerKids && m._runNode && !internalFlag[kids[i]]) continue;
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
  // Honour ``_depth`` if the backend already computed it (the layout
  // pass in webui/_graph_layout.py adds depth/lane to every graph
  // entry it emits); otherwise fall back to "one row per node in
  // list order", matching the original behaviour.
  let maxRow = 0;
  let anyAuthoritative = false;
  ordered.forEach((m) => {
    const n = byId[m.id];
    if (!n) return;
    if (typeof n._depth === "number") {
      anyAuthoritative = true;
      if (n._depth > maxRow) maxRow = n._depth;
    }
  });
  if (anyAuthoritative) {
    Object.keys(byId).forEach((id) => {
      const n = byId[id];
      if (typeof n._depth !== "number") n._depth = 0;
      else if (n._depth > maxRow) maxRow = n._depth;
    });
    return maxRow;
  }
  // Legacy fallback — never hit when backend layout is in play.
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

function _assignLanes(
  byId: Record<string, GNode>,
  headId: string | null,
): { leaves: GNode[]; laneCount: number; leafOfNode: Record<string, string> } {
  const leaves: GNode[] = [];
  Object.keys(byId).forEach((id) => {
    if (!byId[id].children!.length) leaves.push(byId[id]);
  });

  const leafOfNode: Record<string, string> = Object.create(null);
  let laneCount = 1;

  // Detect whether the backend's _graph_layout pass already filled
  // ``_lane`` on these nodes. If so we MUST NOT overwrite it — the
  // old "pin head's ancestry to lane 0" block (preserved below for
  // the legacy no-backend-layout case) would otherwise flatten
  // every retry sibling that sits on a head-ancestor onto column 0.
  const backendLanesPreset = Object.keys(byId).some(
    (id) => typeof byId[id]._lane === "number",
  );

  // ── lane 0 = the trunk = the HEAD conversation chain ───────────────
  // (Legacy path — only when backend didn't pre-compute lanes.)
  let trunkTip: GNode | null =
    headId && byId[headId] ? byId[headId] : null;
  if (!trunkTip) {
    trunkTip = leaves
      .slice()
      .sort((a, b) => (b.created_at || 0) - (a.created_at || 0))[0] || null;
  }
  if (trunkTip) {
    let cur: GNode | null = trunkTip;
    while (cur) {
      if (!backendLanesPreset) cur._lane = 0;
      leafOfNode[cur.id] = trunkTip.id;
      cur = cur.parent_id ? byId[cur.parent_id] : null;
    }
  }

  // Honour backend-supplied lanes if the layout pass attached
  // ``_lane`` to every node. The leafOfNode map is still built
  // locally for click-to-checkout (we need a leaf id per node).
  leaves.forEach((leaf) => {
    let cur: GNode | null = leaf;
    while (cur && !(cur.id in leafOfNode)) {
      leafOfNode[cur.id] = leaf.id;
      cur = cur.parent_id ? byId[cur.parent_id] : null;
    }
  });

  const backendLanes = Object.keys(byId).some(
    (id) => typeof byId[id]._lane === "number",
  );
  if (backendLanes) {
    Object.keys(byId).forEach((id) => {
      const n = byId[id];
      if (typeof n._lane !== "number") n._lane = 0;
      if ((n._lane as number) + 1 > laneCount) laneCount = (n._lane as number) + 1;
    });
  } else {
    // Legacy fallback (was leaf-based lane assignment): never reached
    // when backend layout is in play.
    const leafLane: Record<string, number> = Object.create(null);
    const trunkLeafId = trunkTip?.id || null;
    if (trunkLeafId) leafLane[trunkLeafId] = 0;
    leaves
      .filter((l) => l.id !== trunkLeafId)
      .sort((a, b) => (a.created_at || 0) - (b.created_at || 0))
      .forEach((leaf, i) => {
        leafLane[leaf.id] = i + 1;
      });
    laneCount = Math.max(laneCount, Object.keys(leafLane).length);
    Object.keys(byId).forEach((id) => {
      const lid = leafOfNode[id];
      const lane = lid && lid in leafLane ? leafLane[lid] : 0;
      byId[id]._lane = lane;
      if (lane + 1 > laneCount) laneCount = lane + 1;
    });
  }

  Object.keys(byId).forEach((id) => {
    if (byId[id]._lane === undefined) byId[id]._lane = 0;
    if (!(id in leafOfNode)) leafOfNode[id] = id;
  });

  return { leaves, laneCount, leafOfNode };
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

// Note: _labelFor / _fitLabel previously decided the inline text
// next to each node. With inline labels removed (hover-tooltip
// now), they're unused; deleted to keep the file small.

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
  const lanes = _assignLanes(tree.byId, headId);
  _leafOfNode = lanes.leafOfNode;

  const _colorMap: Record<string, string> = Object.create(null);
  Object.keys(tree.byId).forEach((id) => {
    const node = tree.byId[id];
    if (node._lane !== undefined) {
      _colorMap[id] = _branchColor(node, lanes.leafOfNode);
    }
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
  _internalOwner = internalOwner;

  const laneArea = PAD_X + COL_W * Math.max(lanes.laneCount - 1, 0);
  // Tool / nested-call sub-forks add a horizontal offset (see
  // ``pos()``). Reserve the widest tier offset present in the graph
  // so the right edge doesn't clip those nodes.
  let maxTier = 0;
  Object.keys(tree.byId).forEach((id) => {
    const t = tree.byId[id]._tier;
    if (typeof t === "number" && t > maxTier) maxTier = t;
  });
  const subForkMargin = maxTier >= 1
    ? COL_W * 0.7 + Math.max(0, maxTier - 1) * COL_W * 0.5 + NODE_R * 2
    : 0;
  const panelW = (body && body.clientWidth) || 240;
  const width = Math.max(panelW - 4, laneArea + subForkMargin + PAD_X);
  // Extra bottom space so per-branch labels (dy=+22 below their tip
  // node) don't get clipped by the SVG viewport.
  const height = PAD_Y * 2 + ROW_H * maxDepth + 24;

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
    // Tier 0 (user ↔ assistant on the main conversation thread)
    // stays on the lane's main column — no fork. Tier ≥ 1 means
    // we're inside a sub-call (tool, or tool-spawned LLM) and
    // should branch off to the right so the structure is visible:
    //   user (t=0)
    //    │
    //   assistant (t=0)
    //    ├──── tool (t=1)        ← fork right
    //    │     └── sub-LLM (t=2) ← further right
    //    │
    //   user (t=0)               ← back on main column
    const tier = typeof n._tier === "number" ? n._tier : 0;
    const tierOff = tier <= 0 ? 0 : COL_W * 0.7 + (tier - 1) * COL_W * 0.5;
    return {
      x: PAD_X + (n._lane || 0) * COL_W + tierOff,
      y: PAD_Y + (n._depth || 0) * ROW_H,
    };
  }

  Object.keys(tree.byId).forEach((id) => {
    const node = tree.byId[id];
    // Attach pointers are reference-only — not events on the conv
    // chain. We render them as a single dashed edge below; skip the
    // solid parent edge here so they don't look like a square call.
    if (node.function === "attach") return;
    if (!node.parent_id || !tree.byId[node.parent_id]) return;
    const parent = tree.byId[node.parent_id];
    const p = pos(parent);
    const c = pos(node);
    const color = _branchColor(node, lanes.leafOfNode);
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

  // Attach-reference edges: dashed line from the source branch tip
  // toward the attach pointer (the consumer). Direction matches the
  // user mental model "branch A attaches to anchor B" — content
  // flows from source into the target, so the arrow points at the
  // attach pointer.
  //
  // ``attach_ref`` is set by the backend on attach rows
  // (function="attach") and resolves to a node id in the same graph
  // (the branch tip the card embeds). The anchor relationship is
  // already drawn by the parent_id edge above; this one shows what
  // the attach actually references.
  // For each attach pointer, draw a single dashed edge from the
  // source branch tip (where content comes from) to the anchor
  // (where it lands). Direction is conveyed by a marching-ants
  // animation (CSS: stroke-dashoffset over time) — no arrowhead,
  // because the arrow marker was visually noisy and easy to miss.
  // Also paint a small filled dot on the anchor so attaches landing
  // mid-trunk (e.g. beta B attached onto main) leave a visible
  // marker even when no graph child exists at that point.
  function _isConvDescendant(srcId: string, anchorId: string): boolean {
    let cur: string | null | undefined = srcId;
    for (let i = 0; i < 200 && cur; i++) {
      if (cur === anchorId) return true;
      cur = tree.byId[cur]?.parent_id;
    }
    return false;
  }
  Object.keys(tree.byId).forEach((id) => {
    const node = tree.byId[id];
    if (node.function !== "attach") return;
    const ref = node.attach_ref as string | undefined;
    if (!ref) return;
    const src = tree.byId[ref];
    if (!src) return;
    const anchorId = node.parent_id || node.caller;
    const anchor = anchorId ? tree.byId[anchorId] : null;
    if (!anchor) return;
    const srcPos = pos(src);
    const anchorPos = pos(anchor);
    const color = _branchColor(src, lanes.leafOfNode);
    // Dashed edge skipped when the source is already a conv-
    // descendant of the anchor AND this isn't a user-issued manual
    // attach — otherwise the dashed line just overlaps the solid
    // conv-edge that already exists. Manual attaches always get the
    // edge so the user sees what they just did.
    const isManual = !!node.attach_manual;
    const skipEdge = !isManual && _isConvDescendant(ref, anchorId);
    if (!skipEdge) {
      edgeG.appendChild(
        _svg("path", {
          d: _edgePath(srcPos.x, srcPos.y, anchorPos.x, anchorPos.y),
          stroke: color,
          "stroke-width": 1.6,
          fill: "none",
          "stroke-linecap": "round",
          "stroke-dasharray": "4 4",
          opacity: 0.9,
          class: "history-edge attach-edge",
        }),
      );
    }
    // Anchor-side landing dot — ALWAYS draw, even when the dashed
    // edge is skipped, so the trunk (e.g. main) shows a small mark
    // wherever an attach grafts. Without this, an attach onto a
    // mid-trunk turn leaves zero visible signal on the trunk itself.
    edgeG.appendChild(
      _svg("circle", {
        cx: String(anchorPos.x),
        cy: String(anchorPos.y),
        r: "3.5",
        fill: color,
        stroke: "var(--bg-secondary, #1a1a1a)",
        "stroke-width": "1",
        class: "attach-landing-dot",
      }),
    );
  });

  Object.keys(tree.byId).forEach((id) => {
    const node = tree.byId[id];
    // Attach pointers are pure references — represented as a dashed
    // edge above, never as a square node.
    if (node.function === "attach") return;
    const p = pos(node);
    const isHead = id === headId;
    const onHead = !!headAncestors[id];
    const color = _branchColor(node, lanes.leafOfNode);
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
    // Node size tapers with call-stack depth (``_tier``) so nested
    // sub-calls visually nest under their parents: user / top-level
    // assistant are largest, tool calls smaller, tool-spawned
    // sub-LLM calls smaller still. Independent of ``_depth`` (which
    // is purely the visual y row, including tool stacking).
    const tier = typeof node._tier === "number" ? node._tier : 0;
    const tierShrink = Math.min(0.55, tier * 0.18);
    // HEAD outline is drawn separately (see ``_buildShapeEl`` /
    // current-marker logic); don't inflate the inner shape just
    // because it's on HEAD, or tier-1 tool calls visually merge
    // back into the tier-0 main thread.
    const r = NODE_R * (onHead ? 0.85 : 0.7) * (1 - tierShrink);
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

  // Inline labels were removed: hovering a node fires the tooltip
  // (``_showTooltip``) which carries the role + preview text in a
  // floating panel,without the chronic clutter of one text run per
  // node. The wider SVG also lets the lane columns breathe — labels
  // used to dictate svg width.

  (function _drawBranchTags() {
    const sid = HGW.currentSessionId;
    const rows = (sid && HGW._branchesByConv && HGW._branchesByConv[sid]) || [];
    // Draw a tag for every branch tip with a name (whether user-set or
    // auto-derived from the chain tail). Backend already caps at 40
    // chars so labels stay readable.
    const named = rows.filter((r) => !!(r.name && (r.name as string).trim()));
    if (!named.length) return;
    const tagG = _svg("g", { class: "history-branch-tags" });
    named.forEach((b) => {
      const node = b.head_msg_id ? tree.byId[b.head_msg_id] : null;
      if (!node) return;
      const p = pos(node);
      const label = b.name as string;
      // Below the tip, plain text (no background rect) so sibling
      // branches in adjacent lanes (48px apart) don't visually
      // overlap. The text color tracks the branch's lane colour so
      // each label still reads as "belongs to that line".
      const dy = 22;
      const color = _branchColor(node, lanes.leafOfNode);
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
  // "context" mode bypasses chat-scroll entirely: the white-fill
  // marks the nodes the next LLM turn will see as context.
  if (_highlightMode === "context") {
    const newSet: Record<string, boolean> = Object.create(null);
    if (_contextSet) {
      for (const id in _contextSet) newSet[id] = true;
    }
    _setVisibleSet(newSet);
    return;
  }
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
  // Internal execution nodes (an @agentic_function's LLM / tool
  // calls) have no chat bubble of their own — they surface inside
  // their owner turn's runtime block. The DOM scan above can never
  // mark them, so they'd never get the on-screen emphasis. Propagate
  // visibility from each owner to its internal subtree. Looped to a
  // fixpoint so a nested run (owner is itself internal) also resolves.
  for (let pass = 0; pass < 6; pass++) {
    let changed = false;
    for (const internalId in _internalOwner) {
      if (newSet[internalId]) continue;
      if (newSet[_internalOwner[internalId]]) {
        newSet[internalId] = true;
        changed = true;
      }
    }
    if (!changed) break;
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

// Single click on a node: toggle collapse / expand if the node has
// a collapsible subtree. Nothing else — switching branches /
// scrolling to a message moved to double-click (below) so that
// accidental clicks while exploring the graph don't yank the chat
// off to a different turn.
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
  }
});

// Double click on a node: switch HEAD to that branch (or just scroll
// the chat into view when the clicked node already sits on the
// current HEAD chain).
document.addEventListener("dblclick", (e) => {
  const tgt = e.target as HTMLElement;
  const g = tgt.closest && tgt.closest(".history-node");
  if (!g) return;
  const id = g.getAttribute("data-msg-id");
  if (!id) return;
  // The two ``click`` events that precede a dblclick may have
  // toggled a collapsible node twice — net zero — so the visible
  // state is unchanged by the time we get here.
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

export function getHistoryHighlightMode(): HighlightMode {
  return _highlightMode;
}

export function setHistoryHighlightMode(mode: HighlightMode): void {
  if (mode !== "viewport" && mode !== "context") return;
  if (_highlightMode === mode) return;
  _highlightMode = mode;
  // Context mode needs the latest context-range. Refresh on each
  // entry so a freshly switched HEAD shows the right set; the
  // viewport-mode path doesn't depend on it.
  if (mode === "context") {
    const sid = HGW.currentSessionId;
    if (sid) refreshHistoryContextRange(sid);
  }
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
HGW.setHistoryHighlightMode = setHistoryHighlightMode;
HGW.getHistoryHighlightMode = getHistoryHighlightMode;

void _internalSet;
