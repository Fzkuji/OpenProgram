/**
 * Renderer: captions (dag/rendering.md §2③).
 *
 * A call or a message of yours says what it is in a line of annotation
 * grey to the right of its tile — "Ran npm test", "Read a.ts", the start
 * of what you wrote — so an open thread reads like a log without hovering.
 *
 * A caption only takes room nobody else uses: it runs right along its
 * row until the next tile, branch tag or line running down across it,
 * and a tile with too little room goes without. A line passing right
 * where the caption would start (a fork bridge down the gap beside a
 * thread) pushes it past the line instead. The tile an agent stands on carries the agent's bubble
 * instead (render/agents.ts hides its caption).
 */

import { type GNode, COL_W } from "../../types";
import { _svg, _textWidth } from "../shapes";
import { TILE_R, tileFor } from "../tiles";
import { seatOf as seat } from "../seat";

const LAYER = "dag-captions";
const FONT = "10.5px -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', sans-serif";
const GAP = 8;
const MAX_W = 240;
const MIN_W = 36;
const LOOKAHEAD = 8;

type NodeG = SVGGElement & { _nodeData?: GNode };

function fit(text: string, width: number): string {
  if (_textWidth(text, FONT) <= width) return text;
  const chars = Array.from(text);
  while (chars.length > 1 && _textWidth(chars.join("") + "…", FONT) > width) chars.pop();
  return chars.length > 1 ? chars.join("") + "…" : "";
}

type Run = { x: number; y1: number; y2: number };
type Runs = { down: Run[]; across: Array<{ y: number; x1: number }> };

/** The straight runs of every edge in ``world`` — lines, and the legs of
 *  the paths (absolute M/L/Q/C commands, edges.ts): the vertical ones,
 *  and where each horizontal one starts. */
function edgeRuns(world: Element): Runs {
  const runs: Runs = { down: [], across: [] };
  const add = (x1: number, y1: number, x2: number, y2: number) => {
    if (x1 === x2 && y1 !== y2) runs.down.push({ x: x1, y1: Math.min(y1, y2), y2: Math.max(y1, y2) });
    else if (y1 === y2 && x1 !== x2) runs.across.push({ y: y1, x1: Math.min(x1, x2) });
  };
  world.querySelectorAll(".history-edges line").forEach((l) => {
    const n = (a: string) => Number(l.getAttribute(a));
    add(n("x1"), n("y1"), n("x2"), n("y2"));
  });
  world.querySelectorAll(".history-edges path").forEach((p) => {
    const tokens = (p.getAttribute("d") || "").match(/[MLQC]|-?[\d.]+/g) || [];
    let cx = NaN;
    let cy = NaN;
    for (let i = 0; i < tokens.length;) {
      const cmd = tokens[i++];
      const n = cmd === "Q" ? 4 : cmd === "C" ? 6 : 2;
      const nums = tokens.slice(i, i + n).map(Number);
      i += n;
      const [x, y] = nums.slice(-2);
      if (cmd === "L") add(cx, cy, x, y);
      cx = x;
      cy = y;
    }
  });
  return runs;
}

/** Draw the caption layer above ``nodeG``, avoiding tiles, the branch
 *  tags and the edges already in ``world``. */
export function drawCaptions(world: Element, nodeG: Element): void {
  world.querySelector(`:scope > g.${LAYER}`)?.remove();
  const layer = _svg("g", { class: LAYER, "pointer-events": "none" });
  const cells = new Set<string>();
  const nodes: Array<{ g: NodeG; at: { x: number; y: number } }> = [];
  nodeG.querySelectorAll<NodeG>(".history-node").forEach((g) => {
    const at = seat(g);
    if (!at) return;
    cells.add(`${at.x},${at.y}`);
    nodes.push({ g, at });
  });
  // Branch tags, in world units: the pill rect inside each translated tag.
  const tags: Array<{ x1: number; y1: number; y2: number }> = [];
  world.querySelectorAll(".history-branch-tag").forEach((t) => {
    const at = seat(t);
    const r = t.querySelector("rect");
    if (!at || !r) return;
    // The rect's own x: a branch name starts beside its tile, an agent
    // pill is centred (badges.ts).
    const h = Number(r.getAttribute("height")) || 0;
    tags.push({ x1: at.x + (Number(r.getAttribute("x")) || 0), y1: at.y - h / 2, y2: at.y + h / 2 });
  });
  const runs = edgeRuns(world);

  for (const { g, at } of nodes) {
    const node = g._nodeData;
    if (!node) continue;
    const tile = tileFor(node);
    const text = tile.did;
    if (!text) continue;
    // Room to the right: up to the next tile on this row.
    let free = 0;
    while (free < LOOKAHEAD && !cells.has(`${at.x + (free + 1) * COL_W},${at.y}`)) free++;
    let x = at.x + TILE_R + GAP;
    let right = free >= LOOKAHEAD ? x + MAX_W : at.x + (free + 1) * COL_W - TILE_R - GAP;
    for (const t of tags) {
      if (t.y1 < at.y + 7 && t.y2 > at.y - 7 && t.x1 > at.x) right = Math.min(right, t.x1 - GAP);
    }
    const down = runs.down.filter((r) => r.y1 < at.y + 7 && r.y2 > at.y - 7 && r.x > at.x + TILE_R);
    for (const r of down) if (Math.abs(r.x - x) <= 4) x = r.x + 6;
    for (const r of down) if (r.x > x) right = Math.min(right, r.x - 6);
    for (const r of runs.across) {
      if (Math.abs(r.y - at.y) < 6 && r.x1 > x) right = Math.min(right, r.x1 - 6);
    }
    const shown = fit(text, Math.min(MAX_W, right - x));
    if (!shown || right - x < MIN_W) continue;
    const label = _svg("text", {
      // Your messages' words stay readable zoomed out (interaction/view/far-labels.ts).
      x, y: at.y, class: tile.tone === "user" ? "dag-caption is-turn" : "dag-caption",
      "dominant-baseline": "central",
      "data-for": g.getAttribute("data-msg-id") || "",
    });
    label.textContent = shown;
    layer.appendChild(label);
  }
  // Under the agents' layer, over the nodes.
  const agents = world.querySelector(":scope > g.dag-agents");
  world.insertBefore(layer, agents);
}
