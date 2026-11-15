/** Serializable layout geometry. Content remains owned by the tab store. */
export type LayoutNode =
  | { kind: "pane"; id: string; content: string | null }
  | { kind: "split"; id: string; dir: "row" | "col"; children: LayoutNode[]; sizes: number[] };
export type CanvasLayout = { root: LayoutNode; focusedPaneId: string; zoomedPaneId?: string };
export type CanvasSide = "left" | "right" | "top" | "bottom" | "center";
export type CanvasCorner = "top-left" | "top-right" | "bottom-left" | "bottom-right";
/** Where a dock lands: beside a pane, over it ("center"), or in a canvas corner. */
export type CanvasDockSide = CanvasSide | CanvasCorner;
/** macOS Move & Resize: a half or a quarter of the canvas. Fill is zoom. */
export type CanvasTile = Exclude<CanvasSide, "center"> | CanvasCorner;
export type CanvasPreset = "1x1" | "1x2" | "2x1" | "main+2" | "2x2" | "3x3" | "4x4";
export type Rect = { left: number; top: number; width: number; height: number };

const CANVAS_GAP = 6;

export function canvasViewport(width: number, height: number): Rect {
  const padding = CANVAS_GAP;
  return { left: padding, top: padding, width: Math.max(0, width - padding * 2), height: Math.max(0, height - padding * 2) };
}
const id = () => `canvas:${crypto.randomUUID()}`;
export const pane = (content: string | null = null): LayoutNode => ({ kind: "pane", id: id(), content });
export const leaves = (node: LayoutNode): Extract<LayoutNode, { kind: "pane" }>[] => node.kind === "pane" ? [node] : node.children.flatMap(leaves);
/** A single member keeps its layout only while other (empty) panes remain. */
export const holdsLayout = (canvas: CanvasLayout | undefined) => !!canvas && leaves(canvas.root).length > 1;
export function mapNode(node: LayoutNode, target: string, fn: (n: LayoutNode) => LayoutNode): LayoutNode {
  return node.id === target ? fn(node) : node.kind === "pane" ? node : { ...node, children: node.children.map(c => mapNode(c, target, fn)) };
}
export function normalizeNode(node: LayoutNode): LayoutNode {
  if (node.kind === "pane") return node;
  const children: LayoutNode[] = [], sizes: number[] = [];
  const weights = node.children.map((_, i) => Number.isFinite(node.sizes[i]) && node.sizes[i] > 0 ? node.sizes[i] : 1);
  const total = weights.reduce((a, b) => a + b, 0) || 1;
  node.children.forEach((child, i) => {
    const n = normalizeNode(child), weight = weights[i] / total;
    if (n.kind === "split" && n.dir === node.dir) {
      children.push(...n.children); sizes.push(...n.sizes.map(s => weight * s));
    } else { children.push(n); sizes.push(weight); }
  });
  if (!children.length) return pane();
  if (children.length === 1) return children[0];
  return { ...node, children, sizes };
}
export function rowLayout(contents: string[], ratio = 0.5): CanvasLayout {
  const children = contents.map(pane);
  const root = normalizeNode({ kind: "split", id: id(), dir: "row", children,
    sizes: children.length === 2 ? [ratio, 1 - ratio] : children.map(() => 1) });
  return { root, focusedPaneId: leaves(root)[0].id };
}
export function sanitizeLayout(layout: CanvasLayout, alive: Set<string>): CanvasLayout {
  const seen = new Set<string>();
  function visit(n: LayoutNode): LayoutNode {
    if (n.kind === "split") return { ...n, children: n.children.map(visit) };
    const content = n.content && alive.has(n.content) && !seen.has(n.content) ? n.content : null;
    if (content) seen.add(content);
    return { ...n, content };
  }
  const root = normalizeNode(visit(layout.root)), ids = leaves(root).map(p => p.id);
  return { root, focusedPaneId: ids.includes(layout.focusedPaneId) ? layout.focusedPaneId : ids[0],
    zoomedPaneId: ids.includes(layout.zoomedPaneId ?? "") ? layout.zoomedPaneId : undefined };
}
export function removePane(root: LayoutNode, paneId: string): LayoutNode {
  function remove(n: LayoutNode): LayoutNode | null {
    if (n.id === paneId) return null;
    if (n.kind === "pane") return n;
    const remaining = n.children.map(remove), weights = [...n.sizes];
    remaining.forEach((child, i) => {
      if (child) return;
      let neighbor = remaining.findIndex((c, j) => !!c && j > i);
      if (neighbor < 0) neighbor = remaining.findLastIndex(c => !!c);
      if (neighbor >= 0) weights[neighbor] += weights[i];
    });
    const children = remaining.filter((c): c is LayoutNode => c !== null);
    const sizes = weights.filter((_,i) => remaining[i] !== null);
    return children.length ? normalizeNode({ ...n, children, sizes }) : null;
  }
  return remove(root) ?? pane();
}
export const isCorner = (side: string): side is CanvasCorner => side === "top-left" || side === "top-right" || side === "bottom-left" || side === "bottom-right";
const EPS = 0.01;
const pair = (dir: "row" | "col", first: boolean, added: LayoutNode, other: LayoutNode): LayoutNode =>
  ({ kind: "split", id: id(), dir, children: first ? [added, other] : [other, added], sizes: [0.5, 0.5] });
/** Pane rects as fractions of the canvas, ignoring gaps: tiling decides by proportion. */
export function unitRects(root: LayoutNode): Map<string, Rect> {
  const rects = new Map<string, Rect>();
  (function visit(n: LayoutNode, r: Rect) {
    if (n.kind === "pane") { rects.set(n.id, r); return; }
    let offset = 0;
    n.children.forEach((child, i) => {
      const f = n.sizes[i];
      visit(child, n.dir === "row" ? { ...r, left: r.left + offset * r.width, width: r.width * f } : { ...r, top: r.top + offset * r.height, height: r.height * f });
      offset += f;
    });
  })(root, { left: 0, top: 0, width: 1, height: 1 });
  return rects;
}
/** The canvas quarter a corner names. */
export const cornerRect = (corner: CanvasCorner): Rect =>
  ({ left: corner.endsWith("left") ? 0 : 0.5, top: corner.startsWith("top") ? 0 : 0.5, width: 0.5, height: 0.5 });
/** Put `node` into a canvas corner, splitting the pane that holds the corner.
 *  A pane wider and taller than half leaves an empty quarter beside the new
 *  one, which offers the free tabs, as macOS offers windows for the rest of
 *  the screen. */
function placeInCorner(root: LayoutNode, node: LayoutNode, corner: CanvasCorner): LayoutNode {
  const top = corner.startsWith("top"), left = corner.endsWith("left"), rects = unitRects(root);
  const hit = leaves(root).find(p => {
    const r = rects.get(p.id)!;
    return Math.abs((left ? r.left : r.left + r.width) - (left ? 0 : 1)) < EPS && Math.abs((top ? r.top : r.top + r.height) - (top ? 0 : 1)) < EPS;
  }) ?? leaves(root)[0];
  const r = rects.get(hit.id)!;
  const wide = r.width > 0.5 + EPS, tall = r.height > 0.5 + EPS;
  const placed = wide && tall ? pair("row", left, pair("col", top, node, pane()), hit)
    : wide ? pair("row", left, node, hit)
    : pair("col", top, node, hit);
  return normalizeNode(mapNode(root, hit.id, () => placed));
}
/** Place `node` beside `targetId` ("root" = the whole canvas), over it, or in a corner. */
export function placeNode(root: LayoutNode, node: LayoutNode, targetId: string, side: CanvasDockSide): LayoutNode {
  if (isCorner(side)) return placeInCorner(root, node, side);
  if (side === "center") return mapNode(root, targetId, n => n.kind === "pane" && node.kind === "pane" ? { ...n, content: node.content } : n);
  const dir = side === "left" || side === "right" ? "row" : "col";
  return normalizeNode(mapNode(root, targetId === "root" ? root.id : targetId, n => pair(dir, side === "left" || side === "top", node, n)));
}
export function insertPane(root: LayoutNode, targetId: string, side: CanvasDockSide, content: string | null): { root: LayoutNode; paneId: string } {
  if (side === "center") return { root: placeNode(root, pane(content), targetId, side), paneId: targetId };
  const added = pane(content);
  return { root: placeNode(root, added, targetId, side), paneId: added.id };
}
/** Move an existing pane to a half or quarter of the canvas; the other panes
 *  keep their arrangement in the space that is left. A pane already filling
 *  the quarter trades places with the moving one. */
export function tilePane(root: LayoutNode, paneId: string, tile: CanvasTile): { root: LayoutNode; paneId: string } {
  const all = leaves(root), moving = all.find(p => p.id === paneId);
  if (!moving || all.length < 2) return { root, paneId };
  if (isCorner(tile)) {
    const want = cornerRect(tile), rects = unitRects(root);
    const occupant = all.find(p => {
      const r = rects.get(p.id)!;
      return Math.abs(r.left - want.left) < EPS && Math.abs(r.top - want.top) < EPS && Math.abs(r.width - want.width) < EPS && Math.abs(r.height - want.height) < EPS;
    });
    if (occupant?.id === paneId) return { root, paneId };
    if (occupant) {
      const swapped = mapNode(mapNode(root, occupant.id, n => ({ ...n, content: moving.content }) as LayoutNode), paneId, n => ({ ...n, content: occupant.content }) as LayoutNode);
      return { root: swapped, paneId: occupant.id };
    }
  }
  return { root: placeNode(removePane(root, paneId), moving, "root", tile), paneId };
}
/** The layout a dock produces: the sources leave their panes, then the first
 *  source lands at the drop. A whole group dropped as one brings its canvas. */
export function dockLayout(original: CanvasLayout, sourceIds: string[], paneId: string, side: CanvasDockSide, sourceCanvas?: CanvasLayout): CanvasLayout | null {
  const sourceId = sourceIds[0];
  if (!sourceId) return null;
  const target = paneId === "root" ? "root" : leaves(original.root).find(p => p.id === paneId)?.id ?? leaves(original.root)[0].id;
  const sourcePane = leaves(original.root).find(p => p.content === sourceId);
  if (sourceIds.length === 1 && sourcePane?.id === target) return null;
  let root = original.root;
  for (const sid of sourceIds) {
    const p = leaves(root).find(p => p.content === sid);
    if (p) root = removePane(root, p.id);
  }
  const inserted = insertPane(root, target, side, sourceId);
  if (sourceIds.length > 1 && sourceCanvas) {
    inserted.root = mapNode(inserted.root, inserted.paneId, () => sourceCanvas.root);
    inserted.paneId = sourceCanvas.focusedPaneId;
  }
  return { root: inserted.root, focusedPaneId: inserted.paneId };
}
export function presetLayout(preset: CanvasPreset, contents: string[]): CanvasLayout {
  const split = (dir: "row" | "col", children: LayoutNode[], sizes = children.map(() => 1)): LayoutNode => ({ kind: "split", id: id(), dir, children, sizes });
  let root: LayoutNode;
  if (preset === "main+2") root = split("row", [pane(), split("col", [pane(), pane()])], [0.58, 0.42]);
  else { const [rows, cols] = preset.split("x").map(Number); root = split("col", Array.from({ length: rows }, () => split("row", Array.from({ length: cols }, () => pane())))); }
  root = normalizeNode(root);
  leaves(root).forEach((p, i) => { p.content = contents[i] ?? null; });
  return { root, focusedPaneId: leaves(root)[0].id };
}
export function equalize(root: LayoutNode): LayoutNode {
  return root.kind === "pane" ? root : { ...root, children: root.children.map(equalize), sizes: root.children.map(() => 1 / root.children.length) };
}
/** Smallest pane a divider may leave, like a macOS window's minimum size. */
export const PANE_MIN = { width: 240, height: 160 } as const;
/** Pixels a subtree needs along a split direction. */
export function minExtent(node: LayoutNode, horizontal: boolean): number {
  if (node.kind === "pane") return horizontal ? PANE_MIN.width : PANE_MIN.height;
  const parts = node.children.map(child => minExtent(child, horizontal));
  return (node.dir === "row") === horizontal
    ? parts.reduce((a, b) => a + b, 0) + CANVAS_GAP * (parts.length - 1)
    : Math.max(...parts);
}
/** Move a divider. Given the split's span in pixels, it stops where either
 *  side would shrink below its panes' minimum; already-too-small sides
 *  are never squeezed further. */
export function resizeSplit(root: LayoutNode, splitId: string, index: number, delta: number, span?: number): LayoutNode {
  return mapNode(root, splitId, n => {
    if (n.kind !== "split" || index < 0 || index >= n.sizes.length - 1) return n;
    const sizes = [...n.sizes], sum = sizes[index] + sizes[index + 1];
    const tiny = Math.min(0.02, sum / 4);
    const horizontal = n.dir === "row";
    const lo = span ? Math.min(sizes[index], minExtent(n.children[index], horizontal) / span) : tiny;
    const hi = span ? Math.max(sizes[index], sum - minExtent(n.children[index + 1], horizontal) / span) : sum - tiny;
    sizes[index] = Math.min(Math.max(lo, sizes[index] + delta), hi); sizes[index + 1] = sum - sizes[index];
    return { ...n, sizes };
  });
}
export const SNAP_POINTS = [1 / 3, 1 / 2, 2 / 3] as const;
/** A divider drag near a third, half or two thirds of its split sticks there. */
export function snapResize(sizes: number[], index: number, delta: number, span: number, threshold = 10): { delta: number; snap: number | null } {
  const start = sizes.slice(0, index).reduce((a, b) => a + b, 0), pairSize = sizes[index] + sizes[index + 1];
  const boundary = start + sizes[index] + delta;
  for (const point of SNAP_POINTS) {
    if (point <= start || point >= start + pairSize) continue;
    if (Math.abs(boundary - point) * span <= threshold) return { delta: point - start - sizes[index], snap: point };
  }
  return { delta, snap: null };
}
export function canvasGeometry(root: LayoutNode, rect: Rect) {
  const panes = new Map<string, Rect>();
  const dividers: Array<Rect & { splitId: string; index: number; dir: "row" | "col"; span: number; sizes: number[] }> = [];
  function visit(n: LayoutNode, r: Rect) {
    if (n.kind === "pane") { panes.set(n.id, r); return; }
    const horizontal = n.dir === "row", span = Math.max(0, (horizontal ? r.width : r.height) - CANVAS_GAP * (n.children.length - 1));
    let offset = 0;
    n.children.forEach((child, i) => {
      const length = span * n.sizes[i];
      visit(child, horizontal ? { ...r, left: r.left + offset, width: length } : { ...r, top: r.top + offset, height: length });
      offset += length;
      if (i < n.children.length - 1) dividers.push({ ...(horizontal ? { ...r, left: r.left + offset, width: CANVAS_GAP } : { ...r, top: r.top + offset, height: CANVAS_GAP }), splitId: n.id, index: i, dir: n.dir, span, sizes: n.sizes });
      offset += CANVAS_GAP;
    });
  }
  visit(root, rect); return { panes, dividers };
}

/** A rect as CSS lengths of the canvas element it sits in. */
export type CanvasCssRect = { left: string; top: string; width: string; height: string };
/** Every canvas rect is affine in the canvas size: shares of it plus the
 *  fixed gaps. Written as calc(% + px), the browser lays a pane out in the
 *  same pass as its canvas, so a canvas that a rail is resizing never holds
 *  a pane at its old size, even for a frame. The shares come from laying
 *  the canvas out at two sample sizes. */
const SAMPLE = [1000, 3000] as const;
const length = (a: number, b: number, extra: number) => {
  const share = (b - a) / (SAMPLE[1] - SAMPLE[0]);
  const fixed = a - share * SAMPLE[0] + extra;
  return `calc(${+(share * 100).toFixed(4)}% + ${+fixed.toFixed(3)}px)`;
};
function cssRect(a: Rect, b: Rect, inset = 0): CanvasCssRect {
  return {
    left: length(a.left, b.left, inset), top: length(a.top, b.top, inset),
    width: length(a.width, b.width, -2 * inset), height: length(a.height, b.height, -2 * inset),
  };
}
export function canvasCssGeometry(root: LayoutNode, inset = 0) {
  const [small, large] = SAMPLE.map(size => canvasViewport(size, size));
  const a = canvasGeometry(root, small), b = canvasGeometry(root, large);
  const panes = new Map<string, CanvasCssRect>();
  for (const [paneId, rect] of a.panes) panes.set(paneId, cssRect(rect, b.panes.get(paneId)!, inset));
  return {
    viewport: cssRect(small, large, inset),
    panes,
    dividers: a.dividers.map((divider, i) => cssRect(divider, b.dividers[i])),
  };
}
