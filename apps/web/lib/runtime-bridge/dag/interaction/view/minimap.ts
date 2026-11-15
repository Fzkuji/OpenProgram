/**
 * The minimap (dag/rendering.md §13): the whole graph in a corner, the
 * way a strategy game shows its map. Each node is a dot of its tone on
 * the graph's own lines, thin and faint in their branch colours, so the
 * trunk and its branches read at a glance; the part on screen is a frame
 * in the accent colour, and a press or drag on it moves the camera there.
 * Selected nodes show in the accent colour.
 *
 * It is a canvas beside the graph, outside the SVG, so it never pans or
 * zooms with it. ``canvas.ts`` repaints it on every view change; the dots
 * and lines are read once per drawn graph.
 */


import { seatOf } from "../../render/seat";

// The side column's width (dag/panels/tools.css .dag-side-column), so the
// map and the panels under it line up; wide enough for the sheet's
// longest row.
const W = 280;
const H = 160;
const PAD = 8;

interface Dot { x: number; y: number; color: string; head: boolean }
interface Line { path: Path2D; color: string }
interface MapData { dots: Dot[]; lines: Line[]; x0: number; y0: number; x1: number; y1: number; accent: string; look: string }
export interface MapView { tx: number; ty: number; scale: number; w: number; h: number }

const _data = new WeakMap<SVGGElement, MapData>();

/** The map button and M turn the map on and off (keys.ts); the choice
 *  outlives the page. */
const ON_KEY = "dag-minimap-on";
let _on = (() => {
  try { return localStorage.getItem(ON_KEY) === "1"; } catch { return false; }
})();
let _repaint: (() => void) | null = null;
const _listeners = new Set<() => void>();

export function minimapOn(): boolean {
  return _on;
}

/** The map button's pressed state follows the map. */
export function subscribeMinimap(fn: () => void): () => void {
  _listeners.add(fn);
  return () => { _listeners.delete(fn); };
}

/** Repaint with the current view (a selection change, selection.ts). */
export function repaintMinimap(): void {
  _repaint?.();
}

export function toggleMinimap(): void {
  _on = !_on;
  try { localStorage.setItem(ON_KEY, _on ? "1" : "0"); } catch { /* private mode */ }
  _repaint?.();
  _listeners.forEach((fn) => fn());
}

function readGraph(world: SVGGElement): MapData {
  // Colours are read once per graph and look: a theme or accent switch
  // with no repaint must not leave the old palette on the map. The
  // accent lives in the root's inline style, the theme in its attribute.
  const root = document.documentElement;
  const look = (root.getAttribute("data-theme") || "") + "|" + (root.getAttribute("style") || "");
  const cached = _data.get(world);
  if (cached && cached.look === look) return cached;
  // One computed-style read per tone, not per node.
  const toneColor = new Map<string, string>();
  const dots: Dot[] = [];
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  world.querySelectorAll<SVGGElement>(".history-node").forEach((g) => {
    const at = seatOf(g);
    if (!at) return;
    const { x, y } = at;
    const tile = g.querySelector<SVGGElement>(".dag-tile");
    const tone = (tile?.getAttribute("class") || "").match(/\bt-[\w-]+/)?.[0] || "";
    if (!toneColor.has(tone)) {
      toneColor.set(tone, tile ? getComputedStyle(tile).color : "#888");
    }
    dots.push({ x, y, color: toneColor.get(tone)!, head: g.classList.contains("is-head") });
    x0 = Math.min(x0, x); y0 = Math.min(y0, y);
    x1 = Math.max(x1, x); y1 = Math.max(y1, y);
  });
  // The edges in world units, as the SVG draws them (render/edges.ts):
  // straight ``line``s and the fork / attach ``path``s.
  const lines: Line[] = [];
  // A lane colour is a token (var(--lane-N)), which a canvas cannot
  // read: resolve each distinct stroke once through computed style.
  const strokeColor = new Map<string, string>();
  if (typeof Path2D !== "undefined") {
    world.querySelectorAll<SVGElement>(".history-edges .history-edge").forEach((el) => {
      const stroke = el.getAttribute("stroke") || "";
      if (!strokeColor.has(stroke)) {
        strokeColor.set(stroke, (stroke && getComputedStyle(el).stroke) || "#888");
      }
      const color = strokeColor.get(stroke)!;
      if (el.tagName.toLowerCase() === "line") {
        const path = new Path2D();
        path.moveTo(Number(el.getAttribute("x1")), Number(el.getAttribute("y1")));
        path.lineTo(Number(el.getAttribute("x2")), Number(el.getAttribute("y2")));
        lines.push({ path, color });
      } else if (el.getAttribute("d")) {
        lines.push({ path: new Path2D(el.getAttribute("d")!), color });
      }
    });
  }
  const accent = getComputedStyle(world).getPropertyValue("--accent-blue").trim() || "#4a7dfc";
  const data = { dots, lines, x0, y0, x1, y1, accent, look };
  _data.set(world, data);
  return data;
}

/** World → map: one scale for both axes, the box centred. The box is
 *  the graph and the view together, so the frame is never cut off. */
function fitter(b: { x0: number; y0: number; x1: number; y1: number }): { k: number; ox: number; oy: number } {
  const gw = Math.max(1, b.x1 - b.x0);
  const gh = Math.max(1, b.y1 - b.y0);
  const k = Math.min((W - PAD * 2) / gw, (H - PAD * 2) / gh);
  return { k, ox: (W - gw * k) / 2 - b.x0 * k, oy: (H - gh * k) / 2 - b.y0 * k };
}

/** Where the map goes: the side column's first place (dag-view.tsx
 *  `.dag-minimap-slot`), so it scrolls with the panels under it, or —
 *  with no column — beside the graph. */
function mapHome(panel: HTMLElement): HTMLElement {
  return panel.querySelector<HTMLElement>(":scope > .dag-side-column > .dag-minimap-slot") ?? panel;
}

function findMap(panel: HTMLElement): HTMLCanvasElement | null {
  return mapHome(panel).querySelector<HTMLCanvasElement>(":scope > canvas.dag-minimap");
}

function mapCanvas(host: HTMLElement, jump: (x: number, y: number) => void): HTMLCanvasElement {
  const panel = host.parentElement || host;
  const home = mapHome(panel);
  let c = findMap(panel);
  if (c) return c;
  // The column remounts with the graph view; a map left beside the
  // graph, or in an old column, is not reused.
  panel.querySelectorAll("canvas.dag-minimap").forEach((old) => old.remove());
  c = document.createElement("canvas");
  // Born hidden, so the first paint that shows it plays the way in.
  c.className = "dag-minimap is-hidden";
  c.setAttribute("aria-hidden", "true");
  const dpr = window.devicePixelRatio || 1;
  c.width = W * dpr;
  c.height = H * dpr;
  c.style.width = `${W}px`;
  c.style.height = `${H}px`;
  const toWorld = (e: PointerEvent): void => {
    const map = c as HTMLCanvasElement & { _fit?: { k: number; ox: number; oy: number } };
    if (!map._fit) return;
    const r = map.getBoundingClientRect();
    jump((e.clientX - r.left - map._fit.ox) / map._fit.k, (e.clientY - r.top - map._fit.oy) / map._fit.k);
  };
  c.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    e.stopPropagation();
    c!.setPointerCapture(e.pointerId);
    toWorld(e);
  });
  c.addEventListener("pointermove", (e) => {
    if (!c!.hasPointerCapture(e.pointerId)) return;
    // The left button is up but no release arrived: the drag is over.
    if (e.buttons & 1) toWorld(e);
    else c!.releasePointerCapture(e.pointerId);
  });
  // The map holds still under a drag (``paintMinimap``); let go, it
  // refits around where the view ended up.
  c.addEventListener("lostpointercapture", (e) => toWorld(e));
  // A wheel over the map scrolls the side column it sits in, like the
  // panels under it: no handler, so it reaches the column.
  home.prepend(c);
  void c.offsetWidth;
  return c;
}

/** Repaint the map for ``world`` seen through ``view``. */
export function paintMinimap(
  host: HTMLElement,
  world: SVGGElement,
  view: MapView,
  jump: (x: number, y: number) => void,
): void {
  const c = mapCanvas(host, jump) as HTMLCanvasElement & { _fit?: { k: number; ox: number; oy: number } };
  _repaint = () => paintMinimap(host, world, view, jump);
  const d = readGraph(world);
  // What the camera sees, in world units.
  const vx0 = -view.tx / view.scale;
  const vy0 = -view.ty / view.scale;
  const vx1 = (view.w - view.tx) / view.scale;
  const vy1 = (view.h - view.ty) / view.scale;
  // Once on, it never comes and goes under you.
  const hidden = !_on || d.dots.length < 2;
  c.classList.toggle("is-hidden", hidden);
  if (hidden) return;
  // Under a drag the scale stays put, else the map would slide under
  // the pointer as the frame moves.
  const fit = c.matches(":active") && c._fit ? c._fit : fitter({
    x0: Math.min(d.x0, vx0), y0: Math.min(d.y0, vy0),
    x1: Math.max(d.x1, vx1), y1: Math.max(d.y1, vy1),
  });
  c._fit = fit;
  const ctx = c.getContext("2d");
  if (!ctx) return;
  const dpr = c.width / W;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  const r = Math.max(1.5, Math.min(3, 24 * fit.k / 2));
  // Lines under the dots, in world units: 1.2px on the map at any scale.
  ctx.save();
  ctx.setTransform(dpr * fit.k, 0, 0, dpr * fit.k, dpr * fit.ox, dpr * fit.oy);
  ctx.lineWidth = 1.2 / fit.k;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.globalAlpha = 0.6;
  for (const line of d.lines) {
    ctx.strokeStyle = line.color;
    ctx.stroke(line.path);
  }
  ctx.restore();
  for (const dot of d.dots) {
    ctx.fillStyle = dot.color;
    ctx.beginPath();
    ctx.arc(fit.ox + dot.x * fit.k, fit.oy + dot.y * fit.k, r, 0, Math.PI * 2);
    ctx.fill();
  }
  const { accent } = d;
  // HEAD is where you are: a larger dot ringed in the accent colour.
  const head = d.dots.find((dot) => dot.head);
  if (head) {
    ctx.beginPath();
    ctx.arc(fit.ox + head.x * fit.k, fit.oy + head.y * fit.k, r + 2.5, 0, Math.PI * 2);
    ctx.fillStyle = head.color;
    ctx.fill();
    ctx.lineWidth = 2;
    ctx.strokeStyle = accent;
    ctx.stroke();
  }
  // What you selected, larger and in the accent colour (the colour of a
  // box selection), so a selection far off screen still shows here.
  world.querySelectorAll<SVGGElement>(".history-node.is-selected").forEach((g) => {
    const at = seatOf(g);
    if (!at) return;
    ctx.beginPath();
    ctx.arc(fit.ox + at.x * fit.k, fit.oy + at.y * fit.k, r + 2, 0, Math.PI * 2);
    ctx.fillStyle = accent;
    ctx.fill();
  });
  const frame: [number, number, number, number] = [
    fit.ox + vx0 * fit.k, fit.oy + vy0 * fit.k, (vx1 - vx0) * fit.k, (vy1 - vy0) * fit.k,
  ];
  ctx.fillStyle = accent;
  ctx.globalAlpha = 0.12;
  ctx.fillRect(...frame);
  ctx.globalAlpha = 1;
  ctx.strokeStyle = accent;
  ctx.lineWidth = 1.5;
  ctx.strokeRect(...frame);
}

/** The graph view is coming on screen: put the map away at once, without
 *  its exit motion. It is whatever the last visit left — and the first
 *  frames of this one draw the emptied cache before the graph arrives —
 *  so a visible map here would shrink away and pop back in. Hidden, it
 *  comes in once, on the first paint that has a graph. */
export function stowMinimap(panel: HTMLElement): void {
  const c = findMap(panel);
  if (!c || c.classList.contains("is-hidden")) return;
  c.style.transition = "none";
  c.classList.add("is-hidden");
  void c.offsetWidth;
  c.style.transition = "";
}

/** The canvas is gone (empty session): so is its map. */
export function hideMinimap(host: HTMLElement): void {
  findMap(host.parentElement || host)?.classList.add("is-hidden");
}
