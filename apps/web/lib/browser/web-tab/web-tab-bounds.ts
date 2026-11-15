import type { DesktopWebTabCorners } from "@/lib/desktop/desktop-bridge-types";

export interface WebTabBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export function measureWebTabBounds(
  element: Pick<Element, "getBoundingClientRect">,
): WebTabBounds {
  const rect = element.getBoundingClientRect();
  return {
    x: rect.left,
    y: rect.top,
    width: rect.width,
    height: rect.height,
  };
}

/** The rounded card edge the page's bottom corners sit on, if they do: the
 *  nearest clipping ancestor with a rounded bottom whose left, right and
 *  bottom edges are the page's own. The native view escapes that clip, so
 *  the App paints the outside of its arc above the view. */
export function measureWebTabCorners(element: Element): DesktopWebTabCorners | null {
  const rect = element.getBoundingClientRect();
  for (let node = element.parentElement; node; node = node.parentElement) {
    const style = getComputedStyle(node);
    const radius = parseFloat(style.borderBottomLeftRadius);
    if (!(radius > 0) || style.overflow === "visible") continue;
    const box = node.getBoundingClientRect();
    const flush = Math.abs(box.left - rect.left) <= 1 && Math.abs(box.right - rect.right) <= 1
      && Math.abs(box.bottom - rect.bottom) <= 1;
    if (!flush) return null;
    return { radius, outside: backgroundBehind(node), shadow: style.boxShadow === "none" ? "" : style.boxShadow };
  }
  return null;
}

function backgroundBehind(node: Element): string {
  for (let parent = node.parentElement; parent; parent = parent.parentElement) {
    const color = getComputedStyle(parent).backgroundColor;
    if (color && !isTransparent(color)) return color;
  }
  return "transparent";
}

export function isTransparent(color: string): boolean {
  return color === "transparent" || /^rgba\(.*,\s*0\)$/.test(color) || /\/\s*0\)$/.test(color);
}

export function isWebTabOccluded(
  bounds: WebTabBounds,
  occluders: Iterable<Pick<Element, "getBoundingClientRect">>,
): boolean {
  const right = bounds.x + bounds.width;
  const bottom = bounds.y + bounds.height;
  for (const occluder of occluders) {
    const rect = occluder.getBoundingClientRect();
    if (
      rect.width > 0
      && rect.height > 0
      && Math.max(bounds.x, rect.left) < Math.min(right, rect.right)
      && Math.max(bounds.y, rect.top) < Math.min(bottom, rect.bottom)
    ) {
      return true;
    }
  }
  return false;
}

const PLACING = new Set(["transform", "translate", "scale", "left", "top", "width", "height", "inset"]);

/** Whether a running animation is moving or resizing `element` — one on
 *  the element or on any ancestor that animates where things are drawn.
 *  A Web Animation (the overview's zoom, motion's hardware-accelerated
 *  transforms) changes where the element is drawn without touching the
 *  DOM or its layout size, so neither a MutationObserver nor a
 *  ResizeObserver sees it finish. An endless animation (a pulse, a
 *  shimmer) never settles, so it never counts. */
export function isMovedByAnimation(
  element: Element,
  animations: Iterable<Pick<Animation, "playState" | "effect">>,
): boolean {
  for (const animation of animations) {
    if (animation.playState !== "running") continue;
    const effect = animation.effect as KeyframeEffect | null;
    const target = effect?.target;
    if (!effect || !target || !target.contains(element)) continue;
    if (effect.getTiming().iterations === Infinity) continue;
    const moves = effect.getKeyframes().some(frame =>
      Object.keys(frame).some(prop => PLACING.has(prop)));
    if (moves) return true;
  }
  return false;
}
