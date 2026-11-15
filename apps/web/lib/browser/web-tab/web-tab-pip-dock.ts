/**
 * The floating preview's place in the graph view: the side column
 * (dag/panels/tools.css .dag-side-column), right under the minimap and
 * above the legend and the controls sheet, at the column's width. The
 * preview starts there. It holds still under a drag until the pointer has
 * gone a short way, so a nudge does not undock it; then it springs out to
 * the pointer and goes anywhere. Brought back near its place it
 * springs in, and must be taken a little further to come off again.
 */

import type { WebTabPipRect } from "./web-tab-pip-store";

/** The column's width (minimap.ts W) and the default preview's shape. */
export const PIP_DOCK_WIDTH = 280;
export const PIP_DOCK_HEIGHT = 186;
/** How far the pointer must travel before a docked preview comes out:
 *  until then it holds still. */
export const PIP_DOCK_PULL = 36;
/** How near the column's top-right a free preview's top-right must come
 *  to snap in, and how far it must then go to come off again: the gap
 *  between the two keeps it from flickering on the edge. */
export const PIP_DOCK_SNAP = 56;
export const PIP_DOCK_RELEASE = 88;
/** Coming out of the dock, snapping in and coming off: the preview
 *  springs from where it was to where it now belongs, which keeps moving
 *  with the pointer meanwhile. */
export const PIP_DOCK_SPRING = { type: "spring", stiffness: 520, damping: 42, mass: 0.8 } as const;

/** The visible side column in the owner's pane, if its graph is showing. */
export function dockColumn(pane: Element | null): HTMLElement | null {
  const column = pane?.querySelector<HTMLElement>(".dag-side-column");
  return column && column.offsetParent ? column : null;
}

/** Where a docked preview sits, in `origin`'s coordinates: right under
 *  the minimap's place (its height and the column's gap, both folded
 *  away while the map is off), which scrolls with the column. */
export function dockTarget(column: HTMLElement, origin: DOMRect): WebTabPipRect {
  const box = column.getBoundingClientRect();
  const style = getComputedStyle(column);
  const map = column.querySelector<HTMLElement>(":scope > .dag-minimap-slot");
  const top = map
    ? map.getBoundingClientRect().bottom + parseFloat(getComputedStyle(map).marginBottom) + parseFloat(style.rowGap)
    : box.top + parseFloat(style.paddingTop) - column.scrollTop;
  return {
    x: box.right - parseFloat(style.paddingRight) - PIP_DOCK_WIDTH - origin.left,
    y: top - origin.top,
    width: PIP_DOCK_WIDTH,
    height: PIP_DOCK_HEIGHT,
  };
}

/** A free preview at `rect` is close enough to `target` to snap in, or,
 *  already snapped (`held`), still close enough to stay. */
export function nearDock(rect: WebTabPipRect, target: WebTabPipRect, held = false): boolean {
  const gap = Math.hypot(rect.x + rect.width - (target.x + target.width), rect.y - target.y);
  return gap <= (held ? PIP_DOCK_RELEASE : PIP_DOCK_SNAP);
}

/** The pointer has travelled far enough to pull a docked preview out. */
export function pulledOut(dx: number, dy: number): boolean {
  return Math.hypot(dx, dy) >= PIP_DOCK_PULL;
}

/** How much of a docked preview at `slot` the scrolled column hides:
 *  insets from its top and bottom, both zero when it is all in view. */
export function dockClip(slot: DOMRect, column: DOMRect): { top: number; bottom: number } {
  return {
    top: Math.max(0, column.top - slot.top),
    bottom: Math.max(0, slot.bottom - column.bottom),
  };
}
