/**
 * Renderer: agent tokens (dag/rendering.md §4).
 *
 * Every agent that is working stands on the step it is working on: a
 * status dot of its branch colour on the tile's upper-left corner,
 * breathing like the Activity panel's running dot, and beside the tile a
 * bubble saying who and what ("Main agent · Ran npm test"). With nothing
 * running, the main agent's dot rests on HEAD, steady, without a bubble.
 *
 * Agents are read from the whole graph, not from the tiles on screen: a
 * running call folded into its turn's count still names what the agent
 * is doing, and its token stands on the nearest tile that is drawn. A
 * node belongs to the sub-agent whose spawn root its chain leads back
 * to, else to the main agent.
 *
 * The layer is rebuilt from the graph and the node DOM, so the in-place
 * status patch (render/nodes.ts) repaints it without a full render.
 */

import { translateText } from "@/lib/i18n";
import { type GNode } from "../types";
import { isSpawnRoot } from "../passes/thread-nodes";
import { _branchColor, _svg, _textWidth } from "./shapes";
import { TILE_R, spawnName, tileFor } from "./tiles";
import { seatOf as seat } from "./seat";

const LAYER = "dag-agents";
const BUBBLE_FONT = "11px -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', sans-serif";
const BUBBLE_MAX = 260;
const BUBBLE_H = 20;
const MAIN = "";

type NodeG = SVGGElement & { _nodeData?: GNode };

interface Worker {
  agent: string;
  node: GNode;
  order: number;
}

function parentOf(n: GNode): string | null {
  const p = n.predecessor || n.caller;
  return p && p !== "ROOT" ? p : null;
}

/** The spawn root ``n`` works under, or MAIN. */
export function agentOf(n: GNode, byId: Record<string, GNode>): string {
  let cur: GNode | undefined = n;
  for (let i = 0; cur && i < 10000; i++) {
    if (isSpawnRoot(cur)) return cur.id;
    const p = parentOf(cur);
    cur = p ? byId[p] : undefined;
  }
  return MAIN;
}

/** The tile ``n`` is drawn on: its own, or the nearest drawn ancestor's. */
function seatOf(n: GNode, byId: Record<string, GNode>, drawn: Map<string, NodeG>): NodeG | null {
  let cur: GNode | undefined = n;
  for (let i = 0; cur && i < 10000; i++) {
    const g = drawn.get(cur.id);
    if (g) return g;
    const p = parentOf(cur);
    cur = p ? byId[p] : undefined;
  }
  return null;
}

function agentName(agent: string, byId: Record<string, GNode>, drawn: Map<string, NodeG>): string {
  if (agent === MAIN) return translateText("Main agent", "主 agent");
  const shown = drawn.get(agent)?.getAttribute("data-spawn-name");
  if (shown) return shown;
  return spawnName(byId[agent]) || translateText("Sub-agent", "子 agent");
}

/** A marker pinned to a point on its tile (``at``, world units). The
 *  inner group keeps the marker's on-screen size when the canvas zooms
 *  out (``--dag-marker-scale``, interaction/view/canvas.ts), so ``dx``/``dy``
 *  are screen offsets from the pin. */
function marker(at: { x: number; y: number }, cls: string, dx: number, dy: number): {
  outer: SVGElement; inner: SVGElement;
} {
  const outer = _svg("g", { class: cls, transform: `translate(${at.x},${at.y})` });
  const scaled = _svg("g", { class: "dag-agent-mark" });
  const inner = _svg("g", { transform: `translate(${dx},${dy})` });
  scaled.appendChild(inner);
  outer.appendChild(scaled);
  return { outer, inner };
}

/** The agent's dot on its tile's upper-left corner (the app's
 *  ``indicator-dot``); a second agent on the same tile sits beside it.
 *  The pin is the corner itself, in world units, so zooming out the dot
 *  stays on the shrinking tile instead of holding the screen offset the
 *  corner had at 100% and drifting off as a stray dot. */
function token(at: { x: number; y: number }, k: number, color: string): SVGElement {
  const { outer, inner } = marker(
    { x: at.x - TILE_R + 3, y: at.y - TILE_R + 5 }, "dag-agent-token", -k * 9, 0,
  );
  inner.appendChild(_svg("circle", { r: 3.5, fill: color, class: "dag-agent-dot" }));
  return outer;
}

function bubble(at: { x: number; y: number }, k: number, label: string, color: string): SVGElement {
  let text = label;
  while (_textWidth(text, BUBBLE_FONT) > BUBBLE_MAX && text.length > 2) {
    text = text.slice(0, -2) + "…";
  }
  // No chip behind it: the agent's colour as a dot, then the words, with
  // a canvas-colour halo so a line behind them breaks around the letters.
  const { outer, inner: b } = marker({ x: at.x + TILE_R, y: at.y }, "dag-agent-bubble", 8, k * (BUBBLE_H + 4));
  b.appendChild(_svg("circle", { cx: 3, cy: 0, r: 3, fill: color, class: "dag-agent-bubble-dot" }));
  const t = _svg("text", { x: 11, y: 0, "dominant-baseline": "central" });
  t.textContent = text;
  b.appendChild(t);
  return outer;
}

/** Rebuild the token layer above ``nodeG`` from ``graph``'s running nodes. */
export function paintAgents(nodeG: Element, graph: GNode[]): void {
  const world = nodeG.parentNode;
  if (!world) return;
  world.querySelector(`:scope > g.${LAYER}`)?.remove();
  const layer = _svg("g", { class: LAYER, "pointer-events": "none" });

  const byId: Record<string, GNode> = Object.create(null);
  graph.forEach((n) => { byId[n.id] = n; });
  const drawn = new Map<string, NodeG>();
  nodeG.querySelectorAll<NodeG>(".history-node").forEach((g) => {
    const id = g.getAttribute("data-msg-id");
    if (id) drawn.set(id, g);
  });

  // One token per agent, on the step it started last: a running call
  // outranks the running turn that made it.
  const latest = new Map<string, Worker>();
  graph.forEach((n, i) => {
    if ((n as Record<string, unknown>).status !== "running") return;
    const agent = agentOf(n, byId);
    const order = Number(n.created_at) || i;
    const prev = latest.get(agent);
    if (!prev || order >= prev.order) latest.set(agent, { agent, node: n, order });
  });

  // Agents folded onto the same tile stack their bubbles under each other.
  const onSeat = new Map<NodeG, number>();
  const seated = new Set<string>();
  for (const w of latest.values()) {
    const g = seatOf(w.node, byId, drawn);
    const at = g ? seat(g) : null;
    if (!g || !at) continue;
    const k = onSeat.get(g) || 0;
    onSeat.set(g, k + 1);
    seated.add(g.getAttribute("data-msg-id") || "");
    const name = agentName(w.agent, byId, drawn);
    // The drawn node carries the colour of the road it is on (shapes.ts).
    const color = _branchColor((g as NodeG)._nodeData ?? w.node);
    layer.appendChild(token(at, k, color));
    layer.appendChild(bubble(at, k, `${name} · ${tileFor(w.node).doing}`, color));
  }
  // A bubble speaks for its tile: the tile's own caption and the branch
  // name beside it step aside.
  world.querySelectorAll(".dag-caption[data-for], .history-branch-tag[data-for]").forEach((c) => {
    c.classList.toggle("is-hidden", seated.has(c.getAttribute("data-for") || ""));
  });
  if (!latest.size) {
    const head = nodeG.querySelector<NodeG>(".history-node.is-head");
    const at = head ? seat(head) : null;
    if (head?._nodeData && at) {
      layer.appendChild(token(at, 0, _branchColor(head._nodeData)));
      layer.lastElementChild?.classList.add("is-idle");
    }
  }
  world.appendChild(layer);
}
