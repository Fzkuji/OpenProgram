/** Aspect-preserving PiP overlay size. Window height is content + 30 CSS px
 *  (28px chrome + 2px root borders). Native page size/zoom is not changed. */

export type PipRect = { x: number; y: number; width: number; height: number };
export type PipResizeDir = "n" | "ne" | "e" | "se" | "s" | "sw" | "w" | "nw";

export const PIP_HEADER_HEIGHT = 30;
export const PIP_CONTENT_ASPECT = 16 / 9;
export const PIP_RESIZE_DIRS = ["n", "ne", "e", "se", "s", "sw", "w", "nw"] as const;

const SX: Record<PipResizeDir, -1 | 0 | 1> = {
  n: 0, ne: 1, e: 1, se: 1, s: 0, sw: -1, w: -1, nw: -1,
};
const SY: Record<PipResizeDir, -1 | 0 | 1> = {
  n: -1, ne: -1, e: 0, se: 1, s: 1, sw: 1, w: 0, nw: -1,
};

export function pipContentAspect(rect: PipRect): number {
  const contentHeight = rect.height - PIP_HEADER_HEIGHT;
  if (rect.width > 0 && contentHeight > 0) return rect.width / contentHeight;
  return PIP_CONTENT_ASPECT;
}

function minContentWidth(aspect: number, minWidth: number, minHeight: number): number {
  return Math.max(minWidth, Math.max(0, minHeight - PIP_HEADER_HEIGHT) * aspect);
}

function sizeForWidth(
  width: number,
  maxWidth: number,
  maxHeight: number,
  aspect: number,
  minWidth: number,
  minHeight: number,
): { width: number; height: number } {
  const maxW = Math.max(0, maxWidth);
  const maxH = Math.max(0, maxHeight);
  if (maxH <= PIP_HEADER_HEIGHT) {
    return { width: Math.max(0, Math.min(width, maxW)), height: maxH };
  }
  const cap = Math.min(maxW, (maxH - PIP_HEADER_HEIGHT) * aspect);
  let next = Math.max(0, Math.min(width, cap));
  const floor = minContentWidth(aspect, minWidth, minHeight);
  if (floor <= cap && next < floor) next = floor;
  return { width: next, height: next / aspect + PIP_HEADER_HEIGHT };
}

function clampCoord(value: number, start: number, span: number): number {
  const end = start + span;
  return end < start ? start : Math.max(start, Math.min(value, end));
}

function dirOf(dir?: PipResizeDir): PipResizeDir {
  return dir && dir in SX ? dir : "se";
}

function available(origin: PipRect, bounds: PipRect, dir: PipResizeDir): { width: number; height: number } {
  const sx = SX[dir];
  const sy = SY[dir];
  const right = origin.x + origin.width;
  const bottom = origin.y + origin.height;
  const midX = origin.x + origin.width / 2;
  const midY = origin.y + origin.height / 2;
  const boxRight = bounds.x + bounds.width;
  const boxBottom = bounds.y + bounds.height;
  const maxW = sx > 0
    ? boxRight - origin.x
    : sx < 0
      ? right - bounds.x
      : 2 * Math.min(midX - bounds.x, boxRight - midX);
  const maxH = sy > 0
    ? boxBottom - origin.y
    : sy < 0
      ? bottom - bounds.y
      : 2 * Math.min(midY - bounds.y, boxBottom - midY);
  return {
    width: Math.min(bounds.width, Math.max(0, maxW)),
    height: Math.min(bounds.height, Math.max(0, maxH)),
  };
}

function place(origin: PipRect, size: { width: number; height: number }, dir: PipResizeDir): PipRect {
  const sx = SX[dir];
  const sy = SY[dir];
  const x = sx > 0
    ? origin.x
    : sx < 0
      ? origin.x + origin.width - size.width
      : origin.x + origin.width / 2 - size.width / 2;
  const y = sy > 0
    ? origin.y
    : sy < 0
      ? origin.y + origin.height - size.height
      : origin.y + origin.height / 2 - size.height / 2;
  return { x, y, ...size };
}

/** Resize from any edge or corner. Default `se` keeps the previous bottom-right
 *  handle. Corners project (dx, dy) onto the content-aspect direction with
 *  outward-positive signs. Horizontal edges are width-driven; vertical edges
 *  are height-driven. The opposite corner stays put; the opposite edge keeps
 *  its midpoint. */
export function resizePipRect(
  origin: PipRect,
  dx: number,
  dy: number,
  bounds: PipRect,
  minWidth: number,
  minHeight: number,
  dir: PipResizeDir = "se",
): PipRect {
  const handle = dirOf(dir);
  const sx = SX[handle];
  const sy = SY[handle];
  const aspect = pipContentAspect(origin);
  const rawWidth = sx !== 0 && sy !== 0
    ? origin.width + (sx * dx + (sy * dy) / aspect) / (1 + 1 / (aspect * aspect))
    : sy === 0
      ? origin.width + sx * dx
      : Math.max(0, origin.height + sy * dy - PIP_HEADER_HEIGHT) * aspect;
  const max = available(origin, bounds, handle);
  return place(origin, sizeForWidth(rawWidth, max.width, max.height, aspect, minWidth, minHeight), handle);
}

/** Fit size into the parent on both axes without stretching. Position may
 *  move so the window stays inside; resize handles use `resizePipRect` instead. */
export function clampPipRectAspect(
  rect: PipRect,
  bounds: PipRect,
  minWidth: number,
  minHeight: number,
): PipRect {
  const aspect = pipContentAspect(rect);
  const size = sizeForWidth(rect.width, bounds.width, bounds.height, aspect, minWidth, minHeight);
  return {
    x: clampCoord(rect.x, bounds.x, bounds.width - size.width),
    y: clampCoord(rect.y, bounds.y, bounds.height - size.height),
    ...size,
  };
}
