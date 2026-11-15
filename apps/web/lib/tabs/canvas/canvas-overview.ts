import { create } from "zustand";
import { translateText } from "../../i18n";
import type { CanvasDrop } from "./canvas-drag";
import type { Rect } from "./canvas-layout";

/** Window overview, like Mission Control: every window in the strip (a
 *  single tab or a split group) shrinks to a card that keeps its split.
 *  "drag" opens while a tab or a pane is being dragged, and the drop picks
 *  a card's side to merge with or the space between cards to open alone.
 *  "browse" opens from the strip button or a pinch, and a click switches. */
export type OverviewSide = "left" | "right" | "top" | "bottom";
export type OverviewHover = { card: string; side: OverviewSide } | { card: null; side: null } | null;
/** `landing`: the tab a drop is merging into; the overview zooms that
 *  window's card, as it is, back over the canvas from the release.
 *  `landed` turns true once the merge is ready to commit (it may wait on
 *  the desktop shell); it commits when the zoom is done, and the live
 *  canvas then makes room for the docked tab. */
/** `slot`: where among the cards the dragged window would stand on its
 *  own; the cards keep a gap there and move aside as it moves. Null keeps
 *  it where the window was. */
type OverviewState = { mode: "drag" | "browse" | null; sourceIds: string[]; hover: OverviewHover; label: string; landing: string | null; landed: boolean; slot: number | null };

export const useCanvasOverview = create<OverviewState>(() => ({ mode: null, sourceIds: [], hover: null, label: "", landing: null, landed: false, slot: null }));

/** The drop being landed, held from the release until the overview closes;
 *  null when no drop is landing. `apply` is the merge, once it is ready. */
export type OverviewLanding = { drop: CanvasDrop; sourceIds: string[]; apply?: () => void };
let landingFrom: OverviewLanding | null = null;
export const overviewLandingFrom = () => landingFrom;

export function openOverview(mode: "drag" | "browse", sourceIds: string[] = []) {
  const s = useCanvasOverview.getState();
  if (s.mode === mode && s.sourceIds.join("\n") === sourceIds.join("\n")) return;
  useCanvasOverview.setState({ mode, sourceIds, hover: null, label: "", slot: null });
}
/** A held merge still commits when the overview closes early. */
export function closeOverview() {
  const apply = landingFrom?.apply;
  landingFrom = null;
  if (useCanvasOverview.getState().mode) useCanvasOverview.setState({ mode: null, sourceIds: [], hover: null, label: "", landing: null, landed: false, slot: null });
  apply?.();
}
/** A drop onto a card: keep the drag overview up past the end of the drag
 *  and start zooming that window out of its card at once. The merge may
 *  wait on the desktop shell; the zoom is a compositor animation and
 *  plays on through it. */
export function holdOverviewLanding(drop: CanvasDrop, sourceIds: string[]): void {
  if (useCanvasOverview.getState().mode !== "drag") return;
  // Empty space: the tab stands on its own and the overview stays, now
  // browsing, where any card can be picked up again. The dropped tab's
  // card comes back into its gap once its drop commits (commitCanvasDrop).
  if (drop.action === "fill") { useCanvasOverview.setState({ mode: "browse", hover: null, label: "" }); return; }
  if (drop.action !== "dock") return;
  const card = document.querySelector<HTMLElement>(`[data-overview-target="${CSS.escape(drop.targetId)}"] [data-overview-mini]`);
  landingFrom = card && sourceIds.length ? { drop, sourceIds } : null;
  if (landingFrom) useCanvasOverview.setState({ hover: null, landing: drop.targetId, landed: false });
}
export const overviewHeld = () => landingFrom !== null;
/** A drag picked up in the overview ended without a merge: browse on. */
export function browseOverview(): void {
  if (useCanvasOverview.getState().mode) useCanvasOverview.setState({ mode: "browse", sourceIds: [], hover: null, label: "", slot: null });
}
/** The merge is ready: the overview commits it once its zoom is done.
 *  False when no drop is landing, and the caller commits it now. */
export function landOverview(apply: () => void): boolean {
  if (!landingFrom) { closeOverview(); return false; }
  landingFrom.apply = apply;
  useCanvasOverview.setState({ landed: true });
  return true;
}
export const overviewOpen = () => useCanvasOverview.getState().mode !== null;
/** The open overview zooms its window back before it closes; without one
 *  (or in a drag) this is a plain close. */
let zoomOut: (() => void) | null = null;
export function setOverviewZoomOut(handler: (() => void) | null) { zoomOut = handler; }
export function dismissOverview() { if (zoomOut) zoomOut(); else closeOverview(); }

/** Left and right quarters of a card put the tab beside the window; the
 *  middle puts it above or below by the half under the pointer. */
export const OVERVIEW_SIDE = 0.25;
const SIDE_LABEL: Record<OverviewSide, [string, string]> = {
  left: ["on its left", "放到它左边"], right: ["on its right", "放到它右边"],
  top: ["above it", "放到它上面"], bottom: ["below it", "放到它下面"],
};

function setHover(hover: OverviewHover, label: string, slot = useCanvasOverview.getState().slot) {
  const s = useCanvasOverview.getState();
  const same = s.hover?.card === hover?.card && s.hover?.side === hover?.side;
  if (!same || s.label !== label || s.slot !== slot) useCanvasOverview.setState({ hover, label, slot });
}

type Box = { left: number; top: number; width: number; height: number };
/** Where an element is laid out, without the transform framer-motion
 *  moves it with: a card on its way to a new place already counts there,
 *  so the gap never chases the pointer. */
function laidOut(el: HTMLElement, root: HTMLElement, r: DOMRect): Box {
  let left = 0, top = 0;
  for (let node: HTMLElement | null = el; node && node !== root; node = node.offsetParent as HTMLElement | null) { left += node.offsetLeft; top += node.offsetTop; }
  return { left: r.left + left, top: r.top + top, width: el.offsetWidth, height: el.offsetHeight };
}
const outside = (b: Box, x: number, y: number) => Math.hypot(Math.max(b.left - x, 0, x - b.left - b.width), Math.max(b.top - y, 0, y - b.top - b.height));

/** The gap the dragged window would stand in: kept while it is nearer
 *  the pointer than any card, otherwise beside the card nearest the
 *  pointer, on the side the pointer is. */
function slotAt(root: HTMLElement, r: DOMRect, x: number, y: number, cards: HTMLElement[]): number | null {
  const gap = root.querySelector<HTMLElement>("[data-overview-slot]");
  const current = useCanvasOverview.getState().slot;
  let best = -1, bestDistance = gap ? outside(laidOut(gap, root, r), x, y) : Infinity, after = false;
  cards.forEach((card, i) => {
    const b = laidOut(card, root, r), d = outside(b, x, y);
    if (d < bestDistance) { best = i; bestDistance = d; after = x > b.left + b.width / 2; }
  });
  return best < 0 ? current : best + (after ? 1 : 0);
}

/** The drop under the pointer while the drag overview is open, or null
 *  outside it (the strip, the sidebars, beyond the window). */
export function overviewDropAt(x: number, y: number): CanvasDrop | null {
  const { mode, sourceIds } = useCanvasOverview.getState();
  const root = mode === "drag" ? document.querySelector<HTMLElement>("[data-canvas-overview]") : null;
  if (!root || !sourceIds.length) return null;
  const r = root.getBoundingClientRect();
  if (x < r.left || x > r.right || y < r.top || y > r.bottom) { setHover(null, ""); return null; }
  const cards = [...root.querySelectorAll<HTMLElement>("[data-overview-card]")];
  for (const card of cards) {
    const mini = card.querySelector<HTMLElement>("[data-overview-mini]");
    const b = mini?.getBoundingClientRect();
    if (!b || x < b.left || x > b.right || y < b.top || y > b.bottom) continue;
    const px = (x - b.left) / b.width, py = (y - b.top) / b.height;
    const side: OverviewSide = px < OVERVIEW_SIDE ? "left" : px > 1 - OVERVIEW_SIDE ? "right" : py < 0.5 ? "top" : "bottom";
    const rect: Rect = side === "left" ? { left: b.left, top: b.top, width: b.width / 2, height: b.height }
      : side === "right" ? { left: b.left + b.width / 2, top: b.top, width: b.width / 2, height: b.height }
      : side === "top" ? { left: b.left, top: b.top, width: b.width, height: b.height / 2 }
      : { left: b.left, top: b.top + b.height / 2, width: b.width, height: b.height / 2 };
    const title = card.dataset.overviewTitle ?? "";
    const label = translateText(`Merge with “${title}”, ${SIDE_LABEL[side][0]}`, `和「${title}」合并，${SIDE_LABEL[side][1]}`);
    setHover({ card: card.dataset.overviewCard!, side }, label);
    return { targetId: card.dataset.overviewTarget!, paneId: "root", side, rect, action: "dock", label };
  }
  const label = translateText("Open on its own, here", "单独放在这里");
  const slot = slotAt(root, r, x, y, cards);
  setHover({ card: null, side: null }, label, slot);
  // The window goes before the card that follows the gap in the strip.
  const next = slot === null ? undefined : cards[slot];
  const before = slot === null ? undefined : next?.dataset.overviewFirst ?? null;
  return { targetId: sourceIds[0], paneId: "root", side: "center", rect: { left: r.left, top: r.top, width: r.width, height: r.height }, action: "fill", label, before };
}
