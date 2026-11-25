/** Rectangles of the screenshot overlay: the selection, its handles, and where its bars go. */

export type Rect = { x: number; y: number; width: number; height: number };
export type Handle = "nw" | "n" | "ne" | "e" | "se" | "s" | "sw" | "w";
type Size = { width: number; height: number };

/** The gap between the selection and a bar beside it, and between a bar and the screen's edge. */
export const GAP = 8;
const MIN_SIDE = 4;

export function clampRect(rect: Rect, bounds: Rect): Rect {
  const x0 = Math.max(bounds.x, rect.x);
  const y0 = Math.max(bounds.y, rect.y);
  const x1 = Math.min(bounds.x + bounds.width, rect.x + rect.width);
  const y1 = Math.min(bounds.y + bounds.height, rect.y + rect.height);
  return { x: x0, y: y0, width: Math.max(0, x1 - x0), height: Math.max(0, y1 - y0) };
}

/** The handle within `reach` of the point: corners first, then edges. */
export function handleAt(p: { x: number; y: number }, r: Rect, reach: number): Handle | null {
  const near = (a: number, b: number) => Math.abs(a - b) <= reach;
  const inX = p.x >= r.x - reach && p.x <= r.x + r.width + reach;
  const inY = p.y >= r.y - reach && p.y <= r.y + r.height + reach;
  const left = near(p.x, r.x);
  const right = near(p.x, r.x + r.width);
  const top = near(p.y, r.y);
  const bottom = near(p.y, r.y + r.height);
  if (top && left) return "nw";
  if (top && right) return "ne";
  if (bottom && left) return "sw";
  if (bottom && right) return "se";
  if (top && inX) return "n";
  if (bottom && inX) return "s";
  if (left && inY) return "w";
  if (right && inY) return "e";
  return null;
}

/** Moved by (dx, dy), kept whole inside `bounds`. */
export function moveRect(r: Rect, dx: number, dy: number, bounds: Rect): Rect {
  return {
    ...r,
    x: Math.min(Math.max(bounds.x, r.x + dx), bounds.x + bounds.width - r.width),
    y: Math.min(Math.max(bounds.y, r.y + dy), bounds.y + bounds.height - r.height),
  };
}

/** Resized by dragging `handle` by (dx, dy); dragging past the opposite edge flips it. */
export function resizeRect(r: Rect, handle: Handle, dx: number, dy: number, bounds: Rect): Rect {
  let x0 = r.x;
  let y0 = r.y;
  let x1 = r.x + r.width;
  let y1 = r.y + r.height;
  if (handle.includes("w")) x0 += dx;
  if (handle.includes("e")) x1 += dx;
  if (handle.includes("n")) y0 += dy;
  if (handle.includes("s")) y1 += dy;
  const out = clampRect({
    x: Math.min(x0, x1), y: Math.min(y0, y1), width: Math.abs(x1 - x0), height: Math.abs(y1 - y0),
  }, bounds);
  return { ...out, width: Math.max(MIN_SIDE, out.width), height: Math.max(MIN_SIDE, out.height) };
}

/**
 * Where the tools bar goes: under the selection, left edges lined up; above
 * it when it would not fit under; inside its bottom when it fits on neither
 * side (a selection of the whole screen). Always kept on the screen.
 */
export function placeToolbar(selection: Rect, bar: Size, screen: Rect): { x: number; y: number; side: "below" | "above" | "inside" } {
  const x = Math.min(Math.max(screen.x + GAP, selection.x), screen.x + screen.width - bar.width - GAP);
  const below = selection.y + selection.height + GAP;
  if (below + bar.height <= screen.y + screen.height - GAP) return { x, y: below, side: "below" };
  const above = selection.y - GAP - bar.height;
  if (above >= screen.y + GAP) return { x, y: above, side: "above" };
  const inside = Math.max(screen.y + GAP, selection.y + selection.height - GAP - bar.height);
  return { x, y: inside, side: "inside" };
}

/** Where a panel beside the selection goes: to its right, else its left, else inside its top right. */
export function placeBeside(selection: Rect, panel: Size, screen: Rect): { x: number; y: number } {
  const y = Math.min(Math.max(screen.y + GAP, selection.y), screen.y + screen.height - panel.height - GAP);
  const right = selection.x + selection.width + GAP;
  if (right + panel.width <= screen.x + screen.width - GAP) return { x: right, y };
  const left = selection.x - GAP - panel.width;
  if (left >= screen.x + GAP) return { x: left, y };
  return {
    x: Math.max(screen.x + GAP, selection.x + selection.width - panel.width - GAP),
    y: Math.max(screen.y + GAP, selection.y + GAP),
  };
}
